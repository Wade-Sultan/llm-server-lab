from __future__ import annotations

import asyncio
import logging
import os
import uuid
from decimal import Decimal
from typing import NamedTuple

from app.core.db import AsyncSessionLocal
from app.crud import discovery as crud
from app.crud.components import _normalize
from app.services.chat_models import ChatModelConfig
from app.services.discovery.dedup import filter_new_names, match_item
from app.services.discovery.extract import (
    extract_candidate_names,
    extract_from_source,
    unwrap,
)
from app.services.discovery.fetch import fetch_document
from app.services.discovery.reconcile import reconcile
from app.services.discovery.search import (
    DiscoveryConfigError,
    SearchResult,
    search_launch_pages,
    search_spec_pages,
)
from app.services.discovery.validate import validate_item

logger = logging.getLogger(__name__)

_MAX_SOURCES = 3
# Roundups repeat each other, so a fourth page mostly re-lists the first three.
_MAX_SWEEP_PAGES = 3
# Each candidate costs a search plus up to _MAX_SOURCES fetch+extract calls, so
# this is the sweep's real spend dial: one click is up to N times a single-part
# run. Kept as an env knob for the same reason as DISCOVERY_MAX_ITEMS.
_MAX_SWEEP_CANDIDATES = int(os.getenv("DISCOVERY_SWEEP_MAX_CANDIDATES", "8"))


class _ItemOutcome(NamedTuple):
    """Result of discovering one part. error set = nothing was staged."""

    sources_checked: int
    is_new: bool
    error: str | None


class _SweepCandidates(NamedTuple):
    names: list[str]  # post-filter, capped — what the sweep will pay to extract
    pages_checked: int
    enumerated: int  # names the pages yielded before filtering


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


async def start_sweep_run(hint: str | None, category: str) -> uuid.UUID:
    """Same fire-and-forget contract as start_discovery_run, for the sweep.

    Every item a sweep stages hangs off this one run row, so the admin sees one
    line in Recent runs with the whole batch's cost rather than N rows to
    reconcile by hand."""
    run_id = await _create_run("sweep")
    asyncio.create_task(_sweep(run_id, hint, category))
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
    extraction = await extract_from_source(
        doc, category, query, session_id, usage_events
    )
    if extraction is None:
        return None
    values, provenance = unwrap(extraction, result.url)
    if not values:
        return None
    return result.url, values, provenance


async def _discover_one(
    run_id: uuid.UUID,
    query: str,
    category: str,
    session_id: str,
    usage_events: list[dict],
) -> _ItemOutcome:
    """search -> fetch -> extract -> reconcile -> dedup -> stage, for one part.

    Shared by the single-part path and the sweep, which is why "no sources" and
    "nothing extracted" come back as an outcome rather than a run status: for a
    single-part run those end the run, for a sweep they skip one candidate.
    Raises only what the caller must decide about (a missing search key, a DB
    failure) — per-source failures are already absorbed by _process_source.
    """
    results = await search_spec_pages(query, category, max_results=_MAX_SOURCES)
    if not results:
        return _ItemOutcome(0, False, "search returned no usable results")

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
                "discovery run %s: source %s failed",
                run_id,
                result.url,
                exc_info=outcome,
            )
        elif outcome is not None:
            per_source.append(outcome)

    if not per_source:
        return _ItemOutcome(
            len(results), False, "all sources failed fetch or extraction"
        )

    extracted, provenance, confidence, source_urls = reconcile(per_source)
    name = extracted.get("name") or query
    name_normalized = _normalize(name)
    model_number = extracted.get("model_number")

    validation_status, validation_errors = validate_item(category, extracted)

    async with AsyncSessionLocal() as db:
        candidates = await crud.get_dedup_candidates(db, category)

    matched_id, match_method, match_score = match_item(name, model_number, candidates)
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
    return _ItemOutcome(len(results), match_method is None, None)


async def _finalize(
    run_id: uuid.UUID,
    *,
    status: str,
    error_detail: str | None,
    sources_checked: int,
    items_found: int,
    items_new: int,
    usage_events: list[dict],
) -> None:
    """Roll usage into the run row. Guarded so a DB hiccup on the way out can't
    surface as an unawaited exception from a detached task."""
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


async def _run(run_id: uuid.UUID, query: str, category: str) -> None:
    """The single-part pipeline. Never raises — mirrors BuildRecorder._flush:
    any failure lands in the run row's status/error_detail."""
    status = "completed"
    error_detail: str | None = None
    sources_checked = 0
    items_found = 0
    items_new = 0
    usage_events: list[dict] = []

    try:
        outcome = await _discover_one(
            run_id, query, category, str(run_id), usage_events
        )
        sources_checked = outcome.sources_checked
        if outcome.error:
            status, error_detail = "error", outcome.error
        else:
            items_found, items_new = 1, int(outcome.is_new)
    except DiscoveryConfigError as exc:
        status, error_detail = "error", str(exc)
    except Exception as exc:
        logger.exception("discovery run %s failed", run_id)
        status, error_detail = "error", f"{type(exc).__name__}: {exc}"
    finally:
        await _finalize(
            run_id,
            status=status,
            error_detail=error_detail,
            sources_checked=sources_checked,
            items_found=items_found,
            items_new=items_new,
            usage_events=usage_events,
        )


async def _enumerate_candidates(
    category: str, hint: str | None, session_id: str, usage_events: list[dict]
) -> _SweepCandidates:
    """Roundup pages -> product names worth discovering.

    Names only: nothing a roundup says about a part is trusted, because each
    surviving name is re-discovered from its own spec pages afterwards.
    """
    pages = await search_launch_pages(category, hint, max_results=_MAX_SWEEP_PAGES)
    if not pages:
        return _SweepCandidates([], 0, 0)

    docs = await asyncio.gather(
        *(fetch_document(p.url) for p in pages), return_exceptions=True
    )
    name_lists = await asyncio.gather(
        *(
            extract_candidate_names(doc, category, session_id, usage_events)
            for doc in docs
            if not isinstance(doc, BaseException) and doc is not None
        ),
        return_exceptions=True,
    )

    # Page rank order carries through: earlier pages' names get first claim on
    # the candidate cap.
    names: list[str] = []
    for name_list in name_lists:
        if isinstance(name_list, BaseException):
            logger.warning(
                "discovery sweep: name extraction failed", exc_info=name_list
            )
            continue
        names.extend(name_list)

    async with AsyncSessionLocal() as db:
        known = {
            _normalize(c.name) for c in await crud.get_dedup_candidates(db, category)
        }
        known |= await crud.get_pending_names(db, category)

    fresh = filter_new_names(names, known, _MAX_SWEEP_CANDIDATES)
    logger.info(
        "discovery sweep: %d names from %d pages, %d new after filtering",
        len(names),
        len(pages),
        len(fresh),
    )
    return _SweepCandidates(fresh, len(pages), len(names))


async def _sweep(run_id: uuid.UUID, hint: str | None, category: str) -> None:
    """The category sweep: enumerate what's new, then run the single-part
    pipeline over each candidate, staging everything against this run.

    Candidates run serially on purpose — same reasoning as the gap-fill job.
    _discover_one already fans out across three sources internally, so the
    concurrency is there; serialising here keeps search-API rate limits and
    per-click spend predictable. Never raises, for the same reason as _run.
    """
    status = "completed"
    error_detail: str | None = None
    sources_checked = 0
    items_found = 0
    items_new = 0
    usage_events: list[dict] = []

    try:
        session_id = str(run_id)  # groups this run's calls in OpenRouter's dashboard
        found = await _enumerate_candidates(category, hint, session_id, usage_events)
        sources_checked = found.pages_checked

        if found.pages_checked == 0:
            status, error_detail = "error", "sweep search returned no usable pages"
        elif found.enumerated == 0:
            status, error_detail = (
                "error",
                f"no product names found on {found.pages_checked} pages",
            )
        elif not found.names:
            # Not a failure: the sweep worked and the catalog is already current.
            error_detail = (
                f"all {found.enumerated} candidates are already in the catalog "
                "or the review queue"
            )
        else:
            failures: list[str] = []
            for name in found.names:
                try:
                    outcome = await _discover_one(
                        run_id, name, category, session_id, usage_events
                    )
                except DiscoveryConfigError:
                    raise  # every remaining candidate would fail identically
                except Exception as exc:
                    logger.exception(
                        "discovery sweep %s: candidate %r failed", run_id, name
                    )
                    failures.append(f"{name}: {type(exc).__name__}")
                    continue

                sources_checked += outcome.sources_checked
                if outcome.error:
                    failures.append(f"{name}: {outcome.error}")
                else:
                    items_found += 1
                    items_new += int(outcome.is_new)

            if failures:
                # Partial failure stays "completed" when something was staged —
                # same call as the gap-fill job exiting 0 on partial success.
                # The detail names the first few; the rest are in the logs.
                if items_found == 0:
                    status = "error"
                error_detail = (
                    f"{len(failures)} of {len(found.names)} candidates failed: "
                    + "; ".join(failures[:3])
                    + (" ..." if len(failures) > 3 else "")
                )
    except DiscoveryConfigError as exc:
        status, error_detail = "error", str(exc)
    except Exception as exc:
        logger.exception("discovery sweep %s failed", run_id)
        status, error_detail = "error", f"{type(exc).__name__}: {exc}"
    finally:
        await _finalize(
            run_id,
            status=status,
            error_detail=error_detail,
            sources_checked=sources_checked,
            items_found=items_found,
            items_new=items_new,
            usage_events=usage_events,
        )
