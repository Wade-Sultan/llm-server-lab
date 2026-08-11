"""
benchmarks.py
=============
Backfills `benchmark_scores` on CPUs and GPU chipsets that don't have them.

WHY THIS IS A SEPARATE RUN FROM DISCOVERY. Discovery finds products that are not
in the catalog yet and stages them for review. This targets rows that are
already in the catalog and already approved, and enriches one column on them.
Different target set, different source pages (review outlets and results
databases rather than vendor spec sheets), and — because the numbers are
objective and every one of them arrives with a verbatim snippet backing it —
different write semantics: values land on the row directly instead of queueing
for approval.

WHAT DEPENDS ON THIS. services/recommender/scoring.py ranks CPU and GPU
candidates on these numbers and injects the result into every candidate list the
LLM sees. Its dominance gate additionally refuses to fire unless *every*
candidate in a set is scored, so coverage is not cosmetic: a single unmeasured
chipset in a budget band disables the LLM bypass for every build that lands in
that band. Getting coverage to 100% for the parts that actually appear in
candidate sets is what turns the gate on.

PROVENANCE. Source URLs and the snippet behind each figure are written into the
same JSONB under a reserved `_sources` key. CPUBenchmarkScores and
GPUBenchmarkScores both set extra="allow", so this validates; scoring.py reads
only the known suite keys and ignores it. Keeping provenance beside the number
rather than in a side table is what makes a suspicious score auditable months
later without a join.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import AsyncSessionLocal
from app.crud import discovery as crud
from app.models.discovery import DiscoveryRunType
from app.models.pcparts import CPU, GPUChipset
from app.services.chat_models import ChatModelConfig
from app.services.discovery.extract import extract_from_source, unwrap
from app.services.discovery.fetch import fetch_document
from app.services.discovery.reconcile import reconcile
from app.services.discovery.search import SearchResult, search_spec_pages

logger = logging.getLogger(__name__)

# Benchmark figures vary between outlets (different test benches, driver
# versions, ambient conditions), so more sources is a real accuracy win here in
# a way it is not for a spec sheet. reconcile() takes the modal value, which
# needs at least three sources to break a disagreement rather than just detect
# one.
_MAX_SOURCES = 3

# Ceiling per run. Each part costs one search plus up to _MAX_SOURCES
# fetch+extract calls, so this is the spend dial — same role as
# DISCOVERY_SWEEP_MAX_CANDIDATES on the discovery side.
_DEFAULT_LIMIT = 25

# Reserved JSONB key holding per-field provenance. Leading underscore keeps it
# clearly apart from suite names; scoring.py's _as_float would reject the dict
# anyway, but the naming makes the intent readable.
_PROVENANCE_KEY = "_sources"

# category -> (model, pseudo-category for extraction/search)
_TARGETS: dict[str, tuple[type, str]] = {
    "cpu": (CPU, "cpu_benchmark"),
    "gpu_chipset": (GPUChipset, "gpu_benchmark"),
}


@dataclass
class BackfillStats:
    """What one backfill run did."""

    considered: int = 0
    enriched: int = 0
    no_sources: int = 0
    nothing_extracted: int = 0
    sources_checked: int = 0
    usage_events: list[dict] = field(default_factory=list)

    @property
    def cost_usd(self) -> Decimal:
        return sum(
            (Decimal(str(e.get("cost_usd") or 0)) for e in self.usage_events),
            Decimal("0"),
        )


async def find_unscored(
    db: AsyncSession, category: str, limit: int = _DEFAULT_LIMIT
) -> list:
    """Rows of this category with no usable benchmark data.

    Matches both NULL and an empty JSON object: an importer that wrote `{}`
    leaves a row that is non-null but carries nothing, and scoring.py treats the
    two identically, so the backfill must too.
    """
    entry = _TARGETS.get(category)
    if entry is None:
        return []
    model, _ = entry

    stmt = select(model).where(
        or_(
            model.benchmark_scores.is_(None),
            model.benchmark_scores == {},
        )
    )
    # Only CPU subclasses PCPart and carries is_active; GPUChipset is a group
    # table with no such column.
    if model is CPU:
        stmt = stmt.where(CPU.is_active == True)  # noqa: E712
    stmt = stmt.limit(limit)

    return list((await db.execute(stmt)).scalars().all())


async def _process_source(
    result: SearchResult,
    pseudo_category: str,
    target: str,
    session_id: str,
    usage_events: list[dict],
) -> tuple[str, dict, dict] | None:
    """fetch -> extract -> unwrap for one benchmark page. None = skipped."""
    doc = await fetch_document(result.url)
    if doc is None:
        return None
    extraction = await extract_from_source(
        doc, pseudo_category, target, session_id, usage_events
    )
    if extraction is None:
        return None
    values, provenance = unwrap(extraction, result.url)
    if not values:
        return None
    return result.url, values, provenance


async def _backfill_one(
    db: AsyncSession,
    entity,
    pseudo_category: str,
    session_id: str,
    stats: BackfillStats,
) -> bool:
    """Search, extract and write benchmark scores for one part. True if written."""
    target = entity.name
    results = await search_spec_pages(target, pseudo_category, max_results=_MAX_SOURCES)
    if not results:
        stats.no_sources += 1
        logger.info("benchmark backfill: no sources found for %s", target)
        return False

    stats.sources_checked += len(results)
    outcomes = await asyncio.gather(
        *(
            _process_source(r, pseudo_category, target, session_id, stats.usage_events)
            for r in results
        ),
        return_exceptions=True,
    )

    per_source: list[tuple[str, dict, dict]] = []
    for result, outcome in zip(results, outcomes, strict=False):
        if isinstance(outcome, BaseException):
            logger.warning(
                "benchmark backfill: source %s failed for %s",
                result.url,
                target,
                exc_info=outcome,
            )
        elif outcome is not None:
            per_source.append(outcome)

    if not per_source:
        stats.nothing_extracted += 1
        return False

    extracted, provenance, _confidence, source_urls = reconcile(per_source)
    if not extracted:
        stats.nothing_extracted += 1
        return False

    # Merge rather than replace: a row may already carry one suite from an
    # earlier run or a manual entry, and this run may only have found another.
    merged = dict(entity.benchmark_scores or {})
    merged.update(extracted)
    merged[_PROVENANCE_KEY] = {
        **(merged.get(_PROVENANCE_KEY) or {}),
        **provenance,
        "urls": source_urls,
    }
    # Assigned as a fresh dict rather than mutated in place: SQLAlchemy's change
    # detection watches attribute assignment, and an in-place update of the
    # existing JSONB dict would flush as a no-op.
    entity.benchmark_scores = merged
    await db.flush()

    logger.info(
        "benchmark backfill: %s enriched with %s from %d source(s)",
        target,
        ", ".join(sorted(k for k in extracted)),
        len(per_source),
    )
    return True


async def backfill_benchmarks(
    category: str,
    *,
    limit: int = _DEFAULT_LIMIT,
    run_type: str = DiscoveryRunType.BENCHMARKS.value,
) -> BackfillStats:
    """Fill benchmark_scores for unscored parts of one category.

    Creates a discovery_runs row so the spend shows up beside every other
    discovery run rather than being invisible — this makes real search and LLM
    calls and should be as auditable as a sweep.
    """
    stats = BackfillStats()
    if category not in _TARGETS:
        raise ValueError(f"unsupported benchmark backfill category: {category!r}")
    _, pseudo_category = _TARGETS[category]

    # Imported lazily for the same reason runner._pipeline_version does it:
    # dspy_pipeline pulls in dspy, which the discovery path should not force
    # onto module import.
    from app.services.recommender.dspy_pipeline import PIPELINE_VERSION

    async with AsyncSessionLocal() as db:
        run = await crud.create_run(
            db,
            run_type=run_type,
            pipeline_version=PIPELINE_VERSION,
            model_name=ChatModelConfig.get_discovery_extract_model(),
        )
        run_id = run.id

    session_id = str(uuid.uuid4())
    error_detail: str | None = None
    status = "completed"

    try:
        async with AsyncSessionLocal() as db:
            entities = await find_unscored(db, category, limit=limit)
            stats.considered = len(entities)
            if not entities:
                logger.info("benchmark backfill: every %s already has scores", category)
            for entity in entities:
                try:
                    if await _backfill_one(
                        db, entity, pseudo_category, session_id, stats
                    ):
                        stats.enriched += 1
                except Exception:
                    # One part's failure must not abandon the rest of the batch,
                    # and the work already committed for earlier parts stays.
                    logger.exception("benchmark backfill failed for %s", entity.name)
            await db.commit()
    except Exception as exc:
        status = "error"
        error_detail = str(exc)
        logger.exception("benchmark backfill run %s failed", run_id)

    try:
        async with AsyncSessionLocal() as db:
            await crud.finalize_run(
                db,
                run_id,
                status=status,
                error_detail=error_detail,
                sources_checked=stats.sources_checked,
                items_found=stats.considered,
                items_new=stats.enriched,
                total_cost_usd=stats.cost_usd if stats.usage_events else None,
                tokens_in=sum(e.get("tokens_in") or 0 for e in stats.usage_events),
                tokens_out=sum(e.get("tokens_out") or 0 for e in stats.usage_events),
            )
    except Exception:  # pragma: no cover - defensive
        logger.exception("benchmark backfill: finalizing run %s failed", run_id)

    logger.info(
        "benchmark backfill (%s): considered=%d enriched=%d no_sources=%d "
        "nothing_extracted=%d cost=$%s",
        category,
        stats.considered,
        stats.enriched,
        stats.no_sources,
        stats.nothing_extracted,
        stats.cost_usd,
    )
    return stats
