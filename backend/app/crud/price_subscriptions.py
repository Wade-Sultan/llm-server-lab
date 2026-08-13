from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.crud.pricing_etl import TARGET_MODELS
from app.models.price_subscription import (
    STATUS_ACTIVE,
    STATUS_CANCELED,
    STATUS_SENT,
    PriceSubscription,
    PriceSubscriptionTarget,
)


@dataclass(frozen=True)
class TargetInfo:
    kind: str
    id: uuid.UUID
    name: str
    street_price_cents: int | None


async def resolve_target(
    db: AsyncSession, target_kind: str, target_id: uuid.UUID
) -> TargetInfo | None:
    """Look up whatever carries the street price for this (kind, id), or None
    if the kind is unknown or the row doesn't exist. Every model in
    TARGET_MODELS has `name` and `street_price_cents` — that shared shape is
    what lets subscriptions treat parts and groups uniformly."""
    model = TARGET_MODELS.get(target_kind)
    if model is None:
        return None
    row = await db.get(model, target_id)
    if row is None:
        return None
    return TargetInfo(
        kind=target_kind,
        id=target_id,
        name=row.name,
        street_price_cents=row.street_price_cents,
    )


# Recomputed rather than incremented: the counts are a summary of rows that
# already exist, so deriving them in one statement makes drift impossible.
# Incrementing would need every mutation path (subscribe, cancel, alert sent,
# user deleted via ON DELETE CASCADE — which runs no Python at all) to remember
# to adjust it, and the cascade alone makes that unachievable.
_REFRESH_COUNTS_SQL = text(
    """
    INSERT INTO price_subscription_targets AS t
        (target_kind, target_id, active_count, total_count, last_notified_at, updated_at)
    SELECT
        :kind,
        :target_id,
        count(*) FILTER (WHERE status = 'active'),
        count(*),
        max(notified_at),
        now()
    FROM price_subscriptions
    WHERE target_kind = :kind AND target_id = :target_id
    ON CONFLICT (target_kind, target_id) DO UPDATE SET
        active_count = EXCLUDED.active_count,
        total_count = EXCLUDED.total_count,
        last_notified_at = EXCLUDED.last_notified_at,
        updated_at = now()
    """
)


async def refresh_target_counts(
    db: AsyncSession, target_kind: str, target_id: uuid.UUID
) -> None:
    """Bring price_subscription_targets back in step with the subscription rows
    for one target. Callers commit."""
    await db.execute(_REFRESH_COUNTS_SQL, {"kind": target_kind, "target_id": target_id})


async def get_counts(
    db: AsyncSession, target_kind: str, target_id: uuid.UUID
) -> tuple[int, int]:
    """(active, total) subscribers for a target. A target nobody has ever
    watched has no row at all, which is (0, 0), not an error."""
    row = await db.get(PriceSubscriptionTarget, (target_kind, target_id))
    if row is None:
        return 0, 0
    return row.active_count, row.total_count


async def get_active(
    db: AsyncSession, user_id: uuid.UUID, target_kind: str, target_id: uuid.UUID
) -> PriceSubscription | None:
    result = await db.execute(
        select(PriceSubscription).where(
            PriceSubscription.user_id == user_id,
            PriceSubscription.target_kind == target_kind,
            PriceSubscription.target_id == target_id,
            PriceSubscription.status == STATUS_ACTIVE,
        )
    )
    return result.scalar_one_or_none()


async def subscribe(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    target_kind: str,
    target_id: uuid.UUID,
    threshold_cents: int | None,
    baseline_price_cents: int | None,
) -> PriceSubscription:
    """Create — or, when the user already watches this target, retarget — a
    subscription. Idempotent because the natural client behaviour (tapping
    "alert me" twice, or adjusting the threshold) should not be an error, and
    the partial unique index would otherwise make the second call a 500."""
    existing = await get_active(db, user_id, target_kind, target_id)
    if existing is not None:
        existing.threshold_cents = threshold_cents
        # The baseline is re-anchored to today's price on purpose: the user is
        # asking about the drop from here, not from whenever they first asked.
        existing.baseline_price_cents = baseline_price_cents
        await db.commit()
        await db.refresh(existing)
        return existing

    sub = PriceSubscription(
        user_id=user_id,
        target_kind=target_kind,
        target_id=target_id,
        threshold_cents=threshold_cents,
        baseline_price_cents=baseline_price_cents,
        status=STATUS_ACTIVE,
    )
    db.add(sub)
    await db.flush()
    await refresh_target_counts(db, target_kind, target_id)
    await db.commit()
    await db.refresh(sub)
    return sub


async def cancel(
    db: AsyncSession, *, user_id: uuid.UUID, subscription_id: uuid.UUID
) -> bool:
    """Cancel one of the caller's own subscriptions. Returns False for a row
    that isn't theirs or isn't active, which the route turns into a 404 — a
    403 would confirm the id exists."""
    sub = await db.get(PriceSubscription, subscription_id)
    if sub is None or sub.user_id != user_id or sub.status != STATUS_ACTIVE:
        return False
    sub.status = STATUS_CANCELED
    await db.flush()
    await refresh_target_counts(db, sub.target_kind, sub.target_id)
    await db.commit()
    return True


async def list_for_user(
    db: AsyncSession, user_id: uuid.UUID, *, include_inactive: bool = False
) -> list[PriceSubscription]:
    stmt = select(PriceSubscription).where(PriceSubscription.user_id == user_id)
    if not include_inactive:
        stmt = stmt.where(PriceSubscription.status == STATUS_ACTIVE)
    result = await db.execute(stmt.order_by(PriceSubscription.created_at.desc()))
    return list(result.scalars().all())


async def list_active_for_target(
    db: AsyncSession, target_kind: str, target_id: uuid.UUID
) -> list[PriceSubscription]:
    """Everyone waiting on this target. The ETL's read after it applies a new
    price — hence the partial index this matches exactly."""
    result = await db.execute(
        select(PriceSubscription)
        .where(
            PriceSubscription.target_kind == target_kind,
            PriceSubscription.target_id == target_id,
            PriceSubscription.status == STATUS_ACTIVE,
        )
        .order_by(PriceSubscription.created_at.asc())
    )
    return list(result.scalars().all())


async def mark_sent(
    db: AsyncSession, subscription_id: uuid.UUID, *, price_cents: int
) -> None:
    """Retire a subscription that has been alerted on. Called only after
    commerce confirms it accepted the message, so a failed send leaves the row
    active for the next run rather than silently swallowing the alert."""
    sub = await db.get(PriceSubscription, subscription_id)
    if sub is None:
        return
    sub.status = STATUS_SENT
    sub.notified_price_cents = price_cents
    sub.notified_at = func.now()
    sub.notify_attempts = (sub.notify_attempts or 0) + 1
    sub.last_error = None
    await db.flush()
    await refresh_target_counts(db, sub.target_kind, sub.target_id)
    await db.commit()


async def record_failure(
    db: AsyncSession, subscription_id: uuid.UUID, *, error: str
) -> None:
    """Note a failed dispatch without retiring the subscription."""
    sub = await db.get(PriceSubscription, subscription_id)
    if sub is None:
        return
    sub.notify_attempts = (sub.notify_attempts or 0) + 1
    sub.last_error = error[:500]
    await db.commit()
