import uuid

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base

# Price alerts: a user asks to be told when a part gets cheaper, and the pricing
# ETL (app/services/pricing_etl/) is what decides whether that has happened —
# it is the only thing in the system that ever moves street_price_cents, so it
# is the only place where "the price changed" is a fact rather than a guess.
#
# Targets are the same (target_kind, target_id) pair price_checks uses, NOT an
# FK to pc_parts: street_price_cents lives on pc_parts for cpu/motherboard/case/
# cooler/fan and on the group tables for gpu/psu/ram/storage, so a subscription
# has to be able to point at either. See app/crud/pricing_etl.py::TARGET_MODELS
# for the mapping, which is shared rather than duplicated for exactly this
# reason.

# Terminal once notified: an alert fires at most once per subscription. A user
# who wants to keep watching a part re-subscribes, which starts a fresh row with
# a fresh baseline — otherwise a part oscillating around a threshold would mail
# them on every run that nudged it back down.
STATUS_ACTIVE = "active"
STATUS_SENT = "sent"
STATUS_CANCELED = "canceled"


class PriceSubscription(Base):
    __tablename__ = "price_subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # The customer to mail. Commerce resolves this to an address itself (see
    # its /internal/v1/price-alerts handler) rather than the builder passing an
    # email around: users.email is the source of truth for where a person's
    # mail goes, and a copy taken at subscribe time would go stale.
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    target_kind = Column(Text, nullable=False)
    target_id = Column(UUID(as_uuid=True), nullable=False)

    # The price the user asked to be told about. NULL means "any drop", which
    # is evaluated against baseline_price_cents instead.
    threshold_cents = Column(Integer, nullable=True)

    # street_price_cents as it stood when the subscription was created. Without
    # it "any drop" has no reference point, and the alert email has no honest
    # "was $X" to show for a part whose first ETL check is also the one that
    # triggers the alert.
    baseline_price_cents = Column(Integer, nullable=True)

    status = Column(Text, nullable=False, server_default=STATUS_ACTIVE)

    notified_at = Column(DateTime(timezone=True), nullable=True)
    notified_price_cents = Column(Integer, nullable=True)

    # Dispatch failures leave the row active so the next run retries it; these
    # two are what stop a permanently-failing subscription from being invisible.
    notify_attempts = Column(Integer, nullable=False, server_default="0")
    last_error = Column(Text, nullable=True)

    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    user = relationship("User")

    __table_args__ = (
        # The ETL's lookup: "who is watching this target". Partial because a
        # sent/canceled row is never read by the job again.
        Index(
            "ix_price_subscriptions_active_target",
            "target_kind",
            "target_id",
            postgresql_where=text("status = 'active'"),
        ),
        # One live subscription per user per target. Partial so the same user
        # can re-subscribe to a part they were already alerted about — the
        # sent row stays for history and does not block the new one.
        Index(
            "uq_price_subscriptions_active_user_target",
            "user_id",
            "target_kind",
            "target_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )


class PriceSubscriptionTarget(Base):
    """Per-target subscriber counts.

    A denormalization of price_subscriptions, kept because the counts are read
    on part-facing surfaces ("N people are watching this") where a COUNT(*) per
    part would be a per-render aggregate over the whole table. Maintained by
    recomputing from the subscription rows after every mutation rather than by
    incrementing a counter (app/crud/price_subscriptions.py::refresh_target_counts),
    so it cannot drift out of step with what it summarizes.
    """

    __tablename__ = "price_subscription_targets"

    target_kind = Column(Text, primary_key=True)
    target_id = Column(UUID(as_uuid=True), primary_key=True)

    active_count = Column(Integer, nullable=False, server_default="0")
    total_count = Column(Integer, nullable=False, server_default="0")

    last_notified_at = Column(DateTime(timezone=True), nullable=True)

    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
