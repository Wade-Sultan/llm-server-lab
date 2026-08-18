from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import optional_firebase_token, verify_firebase_token
from app.core.db import get_async_db
from app.crud import price_subscriptions as crud
from app.models.price_subscription import PriceSubscription
from app.models.user import User
from app.schemas.price_subscription import (
    PriceSubscriptionCreate,
    PriceSubscriptionOut,
    PriceTargetLookup,
    TargetSubscriberCount,
)

router = APIRouter(tags=["price-subscriptions"])

# "Tell me when this gets cheaper." The alerts themselves are fired by the
# pricing ETL (app/services/pricing_etl/alerts.py) and delivered by commerce;
# these routes only manage who is waiting for what.
#
# The bell on each build-card part row is the client (frontend
# components/assistant-ui/price-alert.tsx), with the settings page listing what
# a user is watching. Dispatch is separately gated by
# settings.PRICE_ALERTS_ENABLED, so subscribing is safe before that is on: rows
# are recorded and evaluated, and no mail leaves the cluster.
#
# Every target the client names is a pc_parts id, because that is the only
# identifier a build carries — resolve_price_target redirects the grouped types
# to the row the ETL actually prices, so what gets stored is always watchable.


async def _resolve_user(db: AsyncSession, token: dict) -> User:
    """The users row for the caller, provisioning one if this is the first
    thing they've done that needs it.

    Same get-by-uid -> get-by-email+link -> create ladder as
    turn_runner.save_turn and commerce's syncAccount: all three write to the
    same users table, and a fourth way of deciding when a row exists is how
    duplicate accounts happen.
    """
    firebase_uid = token.get("uid")
    email = token.get("email")

    user = (
        await db.execute(select(User).where(User.firebase_uid == firebase_uid))
    ).scalar_one_or_none()

    if user is None and email:
        user = (
            await db.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if user is not None:
            user.firebase_uid = firebase_uid
            await db.commit()
            await db.refresh(user)

    if user is None:
        if not email:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Token has no email claim",
            )
        user = User(
            email=email,
            firebase_uid=firebase_uid,
            hashed_password="!firebase_oauth",  # Firebase-only account sentinel
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    return user


async def _existing_user(db: AsyncSession, token: dict) -> User | None:
    """The caller's users row, or None. Unlike _resolve_user this provisions
    nothing: a caller with no row cannot have subscribed to anything, and
    reading their (empty) subscriptions is not a reason to create an account."""
    return (
        await db.execute(select(User).where(User.firebase_uid == token.get("uid")))
    ).scalar_one_or_none()


def _to_out(
    sub: PriceSubscription,
    *,
    target_name: str | None = None,
    current_price_cents: int | None = None,
) -> PriceSubscriptionOut:
    return PriceSubscriptionOut(
        id=sub.id,
        target_kind=sub.target_kind,
        target_id=sub.target_id,
        target_name=target_name,
        threshold_cents=sub.threshold_cents,
        baseline_price_cents=sub.baseline_price_cents,
        current_price_cents=current_price_cents,
        status=sub.status,
        created_at=sub.created_at,
        notified_at=sub.notified_at,
        notified_price_cents=sub.notified_price_cents,
    )


@router.post(
    "/price-subscriptions",
    response_model=PriceSubscriptionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_price_subscription(
    payload: PriceSubscriptionCreate,
    token: dict = Depends(verify_firebase_token),
    db: AsyncSession = Depends(get_async_db),
) -> PriceSubscriptionOut:
    """Watch a part's price. Repeat calls for the same target update the
    threshold and re-anchor the baseline rather than erroring."""
    target = await crud.resolve_price_target(db, payload.target_kind, payload.target_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown part"
        )

    user = await _resolve_user(db, token)
    # target.kind/target.id rather than the payload's: a GPU is watched through
    # its chipset, and storing what the client sent would be a subscription
    # against a column nothing ever writes.
    sub = await crud.subscribe(
        db,
        user_id=user.id,
        target_kind=target.kind,
        target_id=target.id,
        threshold_cents=payload.threshold_cents,
        baseline_price_cents=target.street_price_cents,
    )
    return _to_out(
        sub,
        target_name=target.name,
        current_price_cents=target.street_price_cents,
    )


@router.get("/price-subscriptions", response_model=list[PriceSubscriptionOut])
async def list_price_subscriptions(
    include_inactive: bool = False,
    token: dict = Depends(verify_firebase_token),
    db: AsyncSession = Depends(get_async_db),
) -> list[PriceSubscriptionOut]:
    """The caller's own subscriptions, newest first. Active only by default;
    include_inactive adds the ones already alerted on or canceled."""
    # No row means nothing can have been subscribed, so this is an empty list
    # rather than a reason to provision an account (mirrors /conversations).
    user = await _existing_user(db, token)
    if user is None:
        return []

    subs = await crud.list_for_user(db, user.id, include_inactive=include_inactive)

    out: list[PriceSubscriptionOut] = []
    for sub in subs:
        target = await crud.resolve_target(db, sub.target_kind, sub.target_id)
        out.append(
            _to_out(
                sub,
                target_name=target.name if target else None,
                current_price_cents=target.street_price_cents if target else None,
            )
        )
    return out


# How many parts one lookup will resolve. A build card asks about its own parts
# — eight or so — and the cap is here so a hand-written URL cannot turn one
# request into an unbounded fan-out of per-target queries.
_LOOKUP_LIMIT = 40


@router.get("/price-subscriptions/lookup", response_model=list[PriceTargetLookup])
async def lookup_price_targets(
    part_ids: str,
    token: dict | None = Depends(optional_firebase_token),
    db: AsyncSession = Depends(get_async_db),
) -> list[PriceTargetLookup]:
    """Resolve build-card parts to the targets a user can actually watch.

    One call per card rather than one per part, and optionally authenticated:
    the price and watcher count are public catalog facts, while `subscription`
    is filled in only for a signed-in caller, so the same request serves the
    shared-build page and the chat.

    Parts that resolve to nothing — unpriced, or an exact with no group — are
    omitted rather than returned empty. The card hides the bell for those,
    which is better than offering an alert that could never fire.
    """
    ids: list[uuid.UUID] = []
    for raw in part_ids.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            ids.append(uuid.UUID(raw))
        except ValueError:
            # A build can carry "" for a name the catalog never resolved. Skip
            # it — one unresolvable part is not a bad request for the rest.
            continue
        if len(ids) >= _LOOKUP_LIMIT:
            break

    # The caller's live subscriptions, keyed by target, so N parts cost one
    # query rather than one apiece.
    mine: dict[tuple[str, uuid.UUID], PriceSubscription] = {}
    if token is not None:
        user = await _existing_user(db, token)
        if user is not None:
            for sub in await crud.list_for_user(db, user.id):
                mine[(sub.target_kind, sub.target_id)] = sub

    out: list[PriceTargetLookup] = []
    for part_id in ids:
        target = await crud.resolve_price_target(db, "pc_part", part_id)
        if target is None:
            continue
        active, _total = await crud.get_counts(db, target.kind, target.id)
        sub = mine.get((target.kind, target.id))
        out.append(
            PriceTargetLookup(
                part_id=part_id,
                target_kind=target.kind,
                target_id=target.id,
                target_name=target.name,
                current_price_cents=target.street_price_cents,
                active_count=active,
                subscription=_to_out(
                    sub,
                    target_name=target.name,
                    current_price_cents=target.street_price_cents,
                )
                if sub is not None
                else None,
            )
        )
    return out


@router.delete(
    "/price-subscriptions/{subscription_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    # A 204 must carry no body. response_class stops the default JSON class
    # from serializing `null`; response_model=None is needed on top of it
    # because FastAPI otherwise infers a model from the `-> None` annotation
    # (NoneType is a truthy type object) and rejects the route at import.
    response_class=Response,
    response_model=None,
)
async def delete_price_subscription(
    subscription_id: uuid.UUID,
    token: dict = Depends(verify_firebase_token),
    db: AsyncSession = Depends(get_async_db),
) -> None:
    """Stop watching. Canceled rather than deleted, so the target's total
    subscriber count still reflects that someone once cared."""
    user = await _existing_user(db, token)
    if user is None or not await crud.cancel(
        db, user_id=user.id, subscription_id=subscription_id
    ):
        # 404 for someone else's id too: a 403 would confirm it exists.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found"
        )


@router.get(
    "/price-subscriptions/targets/{target_kind}/{target_id}",
    response_model=TargetSubscriberCount,
)
async def get_target_subscriber_count(
    target_kind: str,
    target_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
) -> TargetSubscriberCount:
    """How many people are watching one part.

    Unauthenticated because it is an aggregate over a public catalog entry —
    the same reason listing reads are public — and it identifies nobody.
    """
    active, total = await crud.get_counts(db, target_kind, target_id)
    return TargetSubscriberCount(
        target_kind=target_kind,
        target_id=target_id,
        active_count=active,
        total_count=total,
    )
