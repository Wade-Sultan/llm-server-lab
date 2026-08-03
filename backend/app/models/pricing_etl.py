import uuid

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base

# The pricing ETL's run/check pair, same shape as discovery_runs/discovered_items
# in app/models/discovery.py. price_checks stores every raw SerpAPI result (not
# just the ones that survive title filtering) so the eventual legitimacy
# classifier has real negative examples to train on, not just what the ETL
# already trusted enough to average.


class PricingRun(Base):
    __tablename__ = "pricing_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    status = Column(Text, nullable=False, server_default="running")

    parts_checked = Column(Integer, nullable=False, server_default="0")
    searches_used = Column(Integer, nullable=False, server_default="0")

    error_detail = Column(Text, nullable=True)

    started_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at = Column(DateTime(timezone=True), nullable=True)

    checks = relationship(
        "PriceCheck",
        back_populates="run",
        cascade="all, delete-orphan",
    )


class PriceCheck(Base):
    __tablename__ = "price_checks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pricing_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Snapshot reference, not an FK — same philosophy as discovered_items.
    # target_kind is one of: pc_part, gpu_chipset, psu_group, ram_group,
    # storage_group (whichever table actually carries street_price_cents for
    # this part_type).
    target_kind = Column(Text, nullable=False)
    target_id = Column(UUID(as_uuid=True), nullable=False, index=True)

    queries_used = Column(ARRAY(Text), nullable=False)

    # Every result SerpAPI returned, before title filtering:
    # [{"title", "extracted_price", "source", "product_link",
    #   "similarity_score", "included_in_stats"}, ...]
    raw_results = Column(JSONB, nullable=False)

    n_results_total = Column(Integer, nullable=False, server_default="0")
    n_results_used = Column(Integer, nullable=False, server_default="0")

    price_mean_cents = Column(Integer, nullable=True)
    price_min_cents = Column(Integer, nullable=True)
    price_max_cents = Column(Integer, nullable=True)
    price_median_cents = Column(Integer, nullable=True)
    price_stddev_cents = Column(Integer, nullable=True)

    applied = Column(Boolean, nullable=False, server_default="false")

    checked_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    run = relationship("PricingRun", back_populates="checks")


class SerpApiQuota(Base):
    """One row per calendar month. search_count is incremented once per
    SerpAPI call (not per result), so it tracks exactly what SerpAPI itself
    bills against the 1000/month cap."""

    __tablename__ = "serpapi_quota"

    month = Column(Date, primary_key=True)
    search_count = Column(Integer, nullable=False, server_default="0")
