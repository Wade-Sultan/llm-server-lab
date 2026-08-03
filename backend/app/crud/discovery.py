from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import func

from app.models.discovery import DiscoveredItem, DiscoveryRun
from app.models.pcparts import CPU, GPU, GPUChipset
from app.services.discovery.dedup import CatalogCandidate


async def create_run(
    db: AsyncSession, *, run_type: str, pipeline_version: str, model_name: str
) -> DiscoveryRun:
    run = DiscoveryRun(
        run_type=run_type,
        status="running",
        pipeline_version=pipeline_version,
        model_name=model_name,
    )
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
    sources_checked: int = 0,
    items_found: int = 0,
    items_new: int = 0,
    total_cost_usd: Decimal | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
) -> None:
    await db.execute(
        update(DiscoveryRun)
        .where(DiscoveryRun.id == run_id)
        .values(
            status=status,
            error_detail=error_detail,
            sources_checked=sources_checked,
            items_found=items_found,
            items_new=items_new,
            total_cost_usd=total_cost_usd,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            finished_at=func.now(),
        )
    )
    await db.commit()


async def get_run(db: AsyncSession, run_id: uuid.UUID) -> DiscoveryRun | None:
    stmt = (
        select(DiscoveryRun)
        .where(DiscoveryRun.id == run_id)
        .options(selectinload(DiscoveryRun.items))
    )
    result = await db.execute(stmt)
    return result.scalars().first()


async def upsert_discovered_item(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    category: str,
    name_normalized: str,
    model_number: str | None,
    extracted_fields: dict,
    field_provenance: dict,
    extraction_confidence: dict | None,
    source_urls: list[str],
    matched_part_id: uuid.UUID | None,
    matched_chipset_id: uuid.UUID | None,
    match_method: str | None,
    match_score: float | None,
    validation_status: str,
    validation_errors: list[dict] | None,
) -> uuid.UUID:
    """Insert a staged item; a re-run of a still-pending item refreshes it in
    place via the partial unique index (reviewed rows sit outside the index
    predicate, so re-discovery after review creates a fresh pending row)."""
    refresh: dict[str, Any] = {
        "run_id": run_id,
        "model_number": model_number,
        "extracted_fields": extracted_fields,
        "field_provenance": field_provenance,
        "extraction_confidence": extraction_confidence,
        "source_urls": source_urls,
        "matched_part_id": matched_part_id,
        "matched_chipset_id": matched_chipset_id,
        "match_method": match_method,
        "match_score": match_score,
        "validation_status": validation_status,
        "validation_errors": validation_errors,
    }
    stmt = (
        insert(DiscoveredItem)
        .values(
            id=uuid.uuid4(),
            category=category,
            name_normalized=name_normalized,
            **refresh,
        )
        .on_conflict_do_update(
            index_elements=["category", "name_normalized"],
            index_where=text("review_status = 'pending'"),
            set_=refresh,
        )
        .returning(DiscoveredItem.id)
    )
    result = await db.execute(stmt)
    await db.commit()
    return result.scalar_one()


async def get_incomplete_items(
    db: AsyncSession, *, limit: int
) -> list[tuple[str, str]]:
    """Pending queue rows whose last extraction failed validation, oldest first.

    These are the catalog's real gaps: the subtype columns backing
    REQUIRED_FIELDS are all nullable=False, so an approved pc_parts row cannot
    be missing one. What *can* be incomplete is an item staged for review whose
    extraction came up short — a re-run gets it a fresh set of sources.

    Returns (search_query, category). The query prefers the original name from
    extracted_fields over name_normalized, which has been lowercased and
    stripped and makes a worse search term.
    """
    query_expr = func.coalesce(
        DiscoveredItem.extracted_fields["name"].astext,
        DiscoveredItem.name_normalized,
    )
    stmt = (
        select(query_expr, DiscoveredItem.category)
        .where(
            DiscoveredItem.review_status == "pending",
            DiscoveredItem.validation_status == "failed",
        )
        .order_by(DiscoveredItem.created_at.asc())
        .limit(limit)
    )
    rows = await db.execute(stmt)
    return list(rows.all())


async def get_pending_names(db: AsyncSession, category: str) -> set[str]:
    """Normalized names already awaiting review in this category.

    A sweep skips these. Re-running one is harmless (the partial unique index
    refreshes the row in place) but not free, and a reviewer gains nothing from
    a second extraction of something already sitting in their queue.
    """
    stmt = select(DiscoveredItem.name_normalized).where(
        DiscoveredItem.category == category,
        DiscoveredItem.review_status == "pending",
    )
    rows = await db.execute(stmt)
    return set(rows.scalars().all())


async def get_dedup_candidates(
    db: AsyncSession, category: str
) -> list[CatalogCandidate]:
    if category == "cpu":
        stmt = select(CPU.id, CPU.name, CPU.model_number).where(CPU.is_active == True)  # noqa: E712
    elif category == "gpu_variant":
        stmt = select(GPU.id, GPU.name, GPU.model_number).where(GPU.is_active == True)  # noqa: E712
    elif category == "gpu_chipset":
        result = await db.execute(select(GPUChipset.id, GPUChipset.name))
        return [CatalogCandidate(id=row[0], name=row[1]) for row in result.all()]
    else:
        return []
    result = await db.execute(stmt)
    return [
        CatalogCandidate(id=row[0], name=row[1], model_number=row[2])
        for row in result.all()
    ]
