from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# The five kinds of thing that carry a street price, mirroring
# app/crud/pricing_etl.py::TARGET_MODELS. Declared as a Literal so a bad kind is
# a 422 from FastAPI's own validation rather than a lookup that finds no rows
# and looks like an empty result.
TargetKind = Literal[
    "pc_part",
    "gpu_chipset",
    "psu_group",
    "ram_group",
    "storage_group",
]


class PriceSubscriptionCreate(BaseModel):
    target_kind: TargetKind
    target_id: uuid.UUID
    # Omitted means "tell me about any drop", evaluated against the target's
    # price at the moment of subscribing.
    threshold_cents: int | None = Field(default=None, ge=1)


class PriceSubscriptionOut(BaseModel):
    id: uuid.UUID
    target_kind: str
    target_id: uuid.UUID
    target_name: str | None = None
    threshold_cents: int | None
    baseline_price_cents: int | None
    current_price_cents: int | None = None
    status: str
    created_at: datetime
    notified_at: datetime | None
    notified_price_cents: int | None


class PriceTargetLookup(BaseModel):
    """Everything the UI needs to offer an alert on one part, in one row.

    Keyed by the part_id the caller asked about, which is not necessarily the
    target that gets watched: grouped parts are redirected to their group (see
    crud.price_subscriptions.resolve_price_target), and the client has to
    subscribe to — and match its own subscriptions against — the resolved pair,
    not the part it started from.

    `current_price_cents` is the catalog street price, which is what alerts are
    evaluated against; a marketplace listing shown beside it on the same card
    may differ.
    """

    part_id: uuid.UUID
    target_kind: str
    target_id: uuid.UUID
    target_name: str
    current_price_cents: int | None
    active_count: int
    # The caller's own live subscription to this target, if they have one.
    # Always null for an unauthenticated request.
    subscription: PriceSubscriptionOut | None = None


class TargetSubscriberCount(BaseModel):
    """How many people are watching one part. active_count is the live figure;
    total_count includes sent and canceled rows, so a part's popularity does
    not appear to collapse the moment its alerts fire."""

    target_kind: str
    target_id: uuid.UUID
    active_count: int
    total_count: int
