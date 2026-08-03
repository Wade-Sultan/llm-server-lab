from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.db import AsyncSessionLocal
from app.crud import pricing_etl as crud
from app.services.pricing_etl import quota, stats, title_match
from app.services.pricing_etl.serpapi_client import SerpApiConfigError, search_shopping

logger = logging.getLogger(__name__)

# How many parts/groups one Cloud Run Job execution attempts, on top of
# whatever's left in the monthly SerpAPI budget. 30/day keeps the monthly
# total (~930) comfortably under the 1000 cap even on a full month, leaving
# headroom for on-demand/ad-hoc checks.
DAILY_BATCH_SIZE = 30

# Grouped types (GPU/PSU/RAM/Storage) can have many board/kit/drive variants
# sharing one group; searching every variant would blow the budget on a
# single group. Cap it and pool whatever comes back.
_MAX_VARIANTS_PER_GROUP = 3

# part_type values that live directly on pc_parts (see crud.NON_GROUPED_MODELS)
_HIGH_NON_GROUPED = ["cpu", "motherboard"]
_LOW_NON_GROUPED = ["cpucooler", "case", "fan"]

# target_kind values for the grouped types (see crud.GROUP_SPECS)
_HIGH_GROUPED = ["gpu_chipset", "ram_group", "storage_group"]
_LOW_GROUPED = ["psu_group"]


@dataclass
class _Target:
    kind: str  # "pc_part" | "gpu_chipset" | "psu_group" | "ram_group" | "storage_group"
    id: uuid.UUID
    queries: list[str]


async def _build_batch(db, limit: int, low_priority_allowed: bool) -> list[_Target]:
    """Oldest-checked-first across every priced type. High-priority types
    (cpu/gpu/motherboard/ram/storage) are always eligible; low-priority types
    (cooler/case/psu/fan) drop out once the month's quota runs low — see
    quota.low_priority_allowed."""
    targets: list[_Target] = []

    non_grouped_types = _HIGH_NON_GROUPED + (
        _LOW_NON_GROUPED if low_priority_allowed else []
    )
    for part in await crud.get_part_candidates(db, non_grouped_types, limit):
        targets.append(_Target(kind="pc_part", id=part.id, queries=[part.name]))

    grouped_kinds = _HIGH_GROUPED + (_LOW_GROUPED if low_priority_allowed else [])
    for kind in grouped_kinds:
        for group in await crud.get_group_candidates(db, kind, limit):
            variant_names = [v.name for v in group.variants if v.is_active][
                :_MAX_VARIANTS_PER_GROUP
            ]
            if variant_names:
                targets.append(_Target(kind=kind, id=group.id, queries=variant_names))

    return targets


async def _check_target(
    db, run_id: uuid.UUID, target: _Target, max_queries: int
) -> tuple[int, bool]:
    """Runs (up to max_queries of) target's searches, scores/filters titles,
    computes stats, and records both the raw check and the applied price.
    Returns (searches_made, matched)."""
    queries = target.queries[:max_queries]
    if not queries:
        return 0, False

    scored_results: list[dict] = []
    included_prices: list[float] = []
    for q in queries:
        results = await search_shopping(q)
        await quota.record_search(db)
        for r in results:
            score = title_match.similarity(q, r.title)
            included = score >= title_match.SIMILARITY_THRESHOLD
            scored_results.append(
                {
                    "title": r.title,
                    "extracted_price": r.extracted_price,
                    "source": r.source,
                    "product_link": r.product_link,
                    "similarity_score": score,
                    "included_in_stats": included,
                }
            )
            if included:
                included_prices.append(r.extracted_price)

    price_stats = stats.compute_stats(included_prices)

    await crud.record_price_check(
        db,
        run_id=run_id,
        target_kind=target.kind,
        target_id=target.id,
        queries_used=queries,
        raw_results=scored_results,
        n_results_total=len(scored_results),
        n_results_used=len(included_prices),
        price_mean_cents=price_stats.mean_cents if price_stats else None,
        price_min_cents=price_stats.min_cents if price_stats else None,
        price_max_cents=price_stats.max_cents if price_stats else None,
        price_median_cents=price_stats.median_cents if price_stats else None,
        price_stddev_cents=price_stats.stddev_cents if price_stats else None,
        applied=price_stats is not None,
    )
    await crud.record_check_outcome(
        db,
        target_kind=target.kind,
        target_id=target.id,
        checked_at=datetime.now(timezone.utc),
        price_cents=price_stats.mean_cents if price_stats else None,
    )

    return len(queries), price_stats is not None


async def run() -> None:
    """The pipeline entrypoint. Never raises — mirrors discovery/runner.py's
    _run: any failure lands in the run row's status/error_detail."""
    async with AsyncSessionLocal() as db:
        run_row = await crud.create_run(db)
        run_id = run_row.id

    status = "completed"
    error_detail: str | None = None
    parts_checked = 0
    searches_used = 0

    try:
        async with AsyncSessionLocal() as db:
            remaining = await quota.get_remaining(db)
            low_ok = await quota.low_priority_allowed(db)

        budget = min(DAILY_BATCH_SIZE, remaining)
        if budget <= 0:
            error_detail = "monthly SerpAPI budget exhausted"
        else:
            async with AsyncSessionLocal() as db:
                targets = await _build_batch(db, budget, low_ok)

            for target in targets:
                remaining_budget = budget - searches_used
                if remaining_budget <= 0:
                    break
                try:
                    async with AsyncSessionLocal() as db:
                        made, matched = await _check_target(
                            db, run_id, target, remaining_budget
                        )
                except SerpApiConfigError:
                    raise
                except Exception:
                    logger.warning(
                        "pricing run %s: check failed for %s %s",
                        run_id,
                        target.kind,
                        target.id,
                        exc_info=True,
                    )
                    continue
                searches_used += made
                if matched:
                    parts_checked += 1

    except SerpApiConfigError as exc:
        status, error_detail = "error", str(exc)
    except Exception as exc:
        logger.exception("pricing run %s failed", run_id)
        status, error_detail = "error", f"{type(exc).__name__}: {exc}"
    finally:
        try:
            async with AsyncSessionLocal() as db:
                await crud.finalize_run(
                    db,
                    run_id,
                    status=status,
                    error_detail=error_detail,
                    parts_checked=parts_checked,
                    searches_used=searches_used,
                )
        except Exception:
            logger.exception("pricing run %s: failed to finalize run row", run_id)
