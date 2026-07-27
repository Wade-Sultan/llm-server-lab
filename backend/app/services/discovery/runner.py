from __future__ import annotations

import asyncio
import logging
import uuid
from decimal import Decimal

from app.core.db import AsyncSessionLocal
from app.crud import discovery as crud
from app.crud.components import _normalize
from app.services.chat_models import ChatModelConfig
from app.services.discovery.dedup import match_item
from app.services.discovery.extract import extract_from_source, unwrap
from app.services.discovery.fetch import fetch_document
from app.services.discovery.reconcile import reconcile
from app.services.discovery.search import (
    DiscoveryConfigError,
    SearchResult,
    search_spec_pages,
)
from app.services.discovery.validate import validate_item

logger = logging.getLogger(__name__)

_MAX_SOURCES = 3


def _pipeline_version() -> str:
    # Function-level import: dspy_pipeline pulls in dspy, which the discovery
    # path shouldn't force onto module import.
    from app.services.recommender.dspy_pipeline import PIPELINE_VERSION

    return PIPELINE_VERSION


async def _create_run(run_type: str) -> uuid.UUID:
    async with AsyncSessionLocal() as db:
        run = await crud.create_run(
            db,
            run_type=run_type,
            pipeline_version=_pipeline_version(),
            model_name=ChatModelConfig.get_discovery_extract_model(),
        )
        return run.id


async def start_discovery_run(query: str, category: str) -> uuid.UUID:
    """Create the run row (committed before the task spawns, so the client can
    poll immediately) and kick off the background pipeline."""
    run_id = await _create_run("on_demand")
    asyncio.create_task(_run(run_id, query, category))
    return run_id


async def run_discovery(
    query: str, category: str, *, run_type: str = "scheduled"
) -> uuid.UUID:
    """Await the pipeline instead of detaching it.

    The API path can fire-and-forget because the client polls the run row. A
    batch job cannot: the container exits when its coroutine returns, and any
    detached task would be killed mid-flight.
    """
    run_id = await _create_run(run_type)
    await _run(run_id, query, category)
    return run_id


async def _process_source(
    result: SearchResult,
    category: str,
    query: str,
    session_id: str,
    usage_events: list[dict],
) -> tuple[str, dict, dict] | None:
    """fetch -> extract -> unwrap for one source. None = source skipped."""
    doc = await fetch_document(result.url)
    if doc is None:
        return None
    extraction = await extract_from_source(doc, category, query, session_id, usage_events)
    if extraction is None:
        return None
    values, provenance = unwrap(extraction, result.url)
    if not values:
        return None
    return result.url, values, provenance


async def _run(run_id: uuid.UUID, query: str, category: str) -> None:
    """The pipeline. Never raises — mirrors BuildRecorder._flush: any failure
    lands in the run row's status/error_detail, and the finalize itself is
    guarded so a DB hiccup can't surface an unawaited exception."""
    status = "completed"
    error_detail: str | None = None
    sources_checked = 0
    items_found = 0
    items_new = 0
    usage_events: list[dict] = []

    try:
        results = await search_spec_pages(query, category, max_results=_MAX_SOURCES)
        if not results:
            status, error_detail = "error", "search returned no usable results"
            return

        sources_checked = len(results)
        session_id = str(run_id)  # groups this run's calls in OpenRouter's dashboard
        outcomes = await asyncio.gather(
            *(
                _process_source(r, category, query, session_id, usage_events)
                for r in results
            ),
            return_exceptions=True,
        )

        per_source: list[tuple[str, dict, dict]] = []
        for result, outcome in zip(results, outcomes, strict=False):
            if isinstance(outcome, BaseException):
                logger.warning(
                    "discovery run %s: source %s failed", run_id, result.url,
                    exc_info=outcome,
                )
            elif outcome is not None:
                per_source.append(outcome)

        if not per_source:
            status, error_detail = "error", "all sources failed fetch or extraction"
            return

        extracted, provenance, confidence, source_urls = reconcile(per_source)
        name = extracted.get("name") or query
        name_normalized = _normalize(name)
        model_number = extracted.get("model_number")

        validation_status, validation_errors = validate_item(category, extracted)

        async with AsyncSessionLocal() as db:
            candidates = await crud.get_dedup_candidates(db, category)

        matched_id, match_method, match_score = match_item(
            name, model_number, candidates
        )
        matched_part_id = matched_id if category != "gpu_chipset" else None
        matched_chipset_id = matched_id if category == "gpu_chipset" else None

        async with AsyncSessionLocal() as db:
            await crud.upsert_discovered_item(
                db,
                run_id=run_id,
                category=category,
                name_normalized=name_normalized,
                model_number=model_number,
                extracted_fields=extracted,
                field_provenance=provenance,
                extraction_confidence=confidence or None,
                source_urls=source_urls,
                matched_part_id=matched_part_id,
                matched_chipset_id=matched_chipset_id,
                match_method=match_method,
                match_score=match_score,
                validation_status=validation_status,
                validation_errors=validation_errors,
            )
        items_found = 1
        items_new = 1 if match_method is None else 0

    except DiscoveryConfigError as exc:
        status, error_detail = "error", str(exc)
    except Exception as exc:
        logger.exception("discovery run %s failed", run_id)
        status, error_detail = "error", f"{type(exc).__name__}: {exc}"
    finally:
        tokens_in = sum(e.get("tokens_in") or 0 for e in usage_events)
        tokens_out = sum(e.get("tokens_out") or 0 for e in usage_events)
        cost = sum(
            (Decimal(str(e.get("cost_usd") or 0)) for e in usage_events),
            Decimal("0"),
        )
        try:
            async with AsyncSessionLocal() as db:
                await crud.finalize_run(
                    db,
                    run_id,
                    status=status,
                    error_detail=error_detail,
                    sources_checked=sources_checked,
                    items_found=items_found,
                    items_new=items_new,
                    total_cost_usd=cost if usage_events else None,
                    tokens_in=tokens_in if usage_events else None,
                    tokens_out=tokens_out if usage_events else None,
                )
        except Exception:
            logger.exception("discovery run %s: failed to finalize run row", run_id)
