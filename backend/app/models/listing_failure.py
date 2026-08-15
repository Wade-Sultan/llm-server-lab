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

# Parts the listings API could not produce a listing for.
#
# Written by commerce (internal/store/store.go), not by anything here — the
# listings API is the only thing that knows a lookup failed. The model exists in
# the builder because app/models is the schema mirror Alembic autogenerates
# against; without it, the next `alembic revision --autogenerate` would propose
# dropping this table.
#
# Current state, not an event log: one row per part, keyed by part_id, with a
# counter. The build card fetches listings per part on every render, so a single
# part with no listing is hit dozens of times a day — an append-only log would
# be almost entirely duplicates, and the digest email built on it would be
# unreadable.

# A part that exists and is recommended, but has nothing active to buy. A
# coverage gap: the fix is to add a listing.
REASON_NO_ACTIVE_LISTING = "no_active_listing"
# The lookup itself failed — a database error, a query timeout. The fix is
# operational, and the part may well be fine.
REASON_LOOKUP_ERROR = "lookup_error"


class ListingLookupFailure(Base):
    __tablename__ = "listing_lookup_failures"

    part_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pc_parts.id", ondelete="CASCADE"),
        primary_key=True,
    )

    # REASON_* above. Overwritten by the latest failure: a part that starts
    # erroring after months of having no listing is best described by what is
    # wrong with it now.
    reason = Column(Text, nullable=False)
    detail = Column(Text, nullable=True)

    occurrences = Column(Integer, nullable=False, server_default="1")

    first_seen_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Set once this row has been included in a digest email. The digest reports
    # each failure once; the admin page is the standing list of what is still
    # open. A failure that recurs after being resolved clears this and is
    # reported again.
    notified_at = Column(DateTime(timezone=True), nullable=True)

    # Set when a listing is created (or reactivated) for the part. Resolved
    # rows are kept rather than deleted: "this part had no listing for three
    # weeks" is the useful history, and deleting would lose first_seen_at.
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    part = relationship("PCPart")

    __table_args__ = (
        # Both readers — the admin page and the digest — want open rows only.
        Index(
            "ix_listing_lookup_failures_open",
            "last_seen_at",
            postgresql_where=text("resolved_at IS NULL"),
        ),
    )
