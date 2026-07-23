from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import func

from app.models.pcparts import (
    CPU,
    Case,
    CPUCooler,
    Fan,
    GPUChipset,
    Motherboard,
    PCPart,
    PSUGroup,
    RAMGroup,
    StorageGroup,
)
from app.models.pricing_etl import PriceCheck, PricingRun, SerpApiQuota

# part_type -> model, for the types whose street_price_cents lives directly on
# the pc_parts row. gpu/psu/ramkit/storagedrive are deliberately absent — their
# price lives on the group (see GROUP_SPECS).
NON_GROUPED_MODELS: dict[str, type[PCPart]] = {
    "cpu": CPU,
    "cpucooler": CPUCooler,
    "motherboard": Motherboard,
    "case": Case,
    "fan": Fan,
}

# target_kind -> (group model, relationship to its exact/variant model). The
# variant relationship is what supplies real, searchable product names — the
# group's own `name` is a spec label, not a marketed product.
GROUP_SPECS: dict[str, tuple[type, Any]] = {
    "gpu_chipset": (GPUChipset, GPUChipset.variants),
    "psu_group": (PSUGroup, PSUGroup.variants),
    "ram_group": (RAMGroup, RAMGroup.variants),
    "storage_group": (StorageGroup, StorageGroup.variants),
}


async def create_run(db: AsyncSession) -> PricingRun:
    run = PricingRun(status="running")
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def finalize_run(
    db: AsyncSession,
    run_id: uuid.UUID,
    *,
    status: str,
    error_detail: str | None = None,
    parts_checked: int = 0,
    searches_used: int = 0,
) -> None:
    run = await db.get(PricingRun, run_id)
    if run is None:
        return
    run.status = status
    run.error_detail = error_detail
    run.parts_checked = parts_checked
    run.searches_used = searches_used
    run.finished_at = func.now()
    await db.commit()


async def get_part_candidates(
    db: AsyncSession, part_types: list[str], limit: int
) -> list[PCPart]:
    """Active parts of the given part_types, oldest-checked-first (never
    checked sorts first via nulls-first ordering)."""
    if limit <= 0 or not part_types:
        return []
    stmt = (
        select(PCPart)
        .where(PCPart.part_type.in_(part_types), PCPart.is_active == True)  # noqa: E712
        .order_by(PCPart.last_price_checked_at.asc().nulls_first())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_group_candidates(
    db: AsyncSession, target_kind: str, limit: int
) -> list[Any]:
    """Groups with at least one active variant, oldest-checked-first, variants
    eager-loaded (the ETL needs each variant's name to build search queries)."""
    if limit <= 0:
        return []
    model, variants_rel = GROUP_SPECS[target_kind]
    variant_model = variants_rel.property.mapper.class_
    stmt = (
        select(model)
        .join(variant_model)
        .where(variant_model.is_active == True)  # noqa: E712
        .options(selectinload(variants_rel))
        .order_by(model.last_price_checked_at.asc().nulls_first())
        .distinct()
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def record_price_check(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    target_kind: str,
    target_id: uuid.UUID,
    queries_used: list[str],
    raw_results: list[dict],
    n_results_total: int,
    n_results_used: int,
    price_mean_cents: int | None,
    price_min_cents: int | None,
    price_max_cents: int | None,
    price_median_cents: int | None,
    price_stddev_cents: int | None,
    applied: bool,
) -> None:
    db.add(
        PriceCheck(
            run_id=run_id,
            target_kind=target_kind,
            target_id=target_id,
            queries_used=queries_used,
            raw_results=raw_results,
            n_results_total=n_results_total,
            n_results_used=n_results_used,
            price_mean_cents=price_mean_cents,
            price_min_cents=price_min_cents,
            price_max_cents=price_max_cents,
            price_median_cents=price_median_cents,
            price_stddev_cents=price_stddev_cents,
            applied=applied,
        )
    )
    await db.commit()


_TARGET_MODELS: dict[str, type] = {
    "pc_part": PCPart,
    **{kind: model for kind, (model, _) in GROUP_SPECS.items()},
}


async def record_check_outcome(
    db: AsyncSession,
    *,
    target_kind: str,
    target_id: uuid.UUID,
    checked_at: datetime,
    price_cents: int | None,
) -> None:
    """Always stamps last_price_checked_at (so an unmatched part isn't picked
    again next run), and additionally writes street_price_cents/price_source
    when the check produced a usable price."""
    model = _TARGET_MODELS[target_kind]
    target = await db.get(model, target_id)
    if target is None:
        return
    target.last_price_checked_at = checked_at
    if price_cents is not None:
        target.street_price_cents = price_cents
        target.price_source = "serpapi"
    await db.commit()


async def get_quota_used(db: AsyncSession, month: Any) -> int:
    row = await db.get(SerpApiQuota, month)
    return row.search_count if row else 0


async def record_search(db: AsyncSession, month: Any) -> None:
    """Atomic upsert increment — safe even if a future run parallelizes."""
    stmt = (
        insert(SerpApiQuota)
        .values(month=month, search_count=1)
        .on_conflict_do_update(
            index_elements=["month"],
            set_={"search_count": SerpApiQuota.search_count + 1},
        )
    )
    await db.execute(stmt)
    await db.commit()
