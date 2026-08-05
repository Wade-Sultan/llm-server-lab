import enum
import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base

# The parts-discovery staging area. Nothing here ever writes to pc_parts
# directly — a discovered item sits in the queue until a human approves it in
# the admin panel, and approval performs the typed pc_parts insert. Staging
# rows are JSONB on purpose: typed columns exist only on the pc_parts subtypes,
# so the approval-time insert is the structural enforcement boundary.
#
# `extracted_fields` and `field_provenance` are snapshots, not references —
# same philosophy as `candidate_set` on module_decisions. The reviewer must see
# exactly what the model saw even if the source page changes later.


class DiscoveryRunType(str, enum.Enum):
    HARDWARE = "hardware"  # scheduled source-monitoring run (monthly job)
    AI_MODELS = "ai_models"  # scheduled Hugging Face Hub run (monthly job)
    ON_DEMAND = "on_demand"  # admin-panel search button
    SWEEP = "sweep"  # admin-panel category sweep (one row, N items)


class DiscoveryRunStatus(str, enum.Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"


class DiscoveryCategory(str, enum.Enum):
    CPU = "cpu"
    GPU_CHIPSET = "gpu_chipset"
    GPU_VARIANT = "gpu_variant"
    AI_MODEL = "ai_model"
    MOTHERBOARD = "motherboard"
    CPU_COOLER = "cpu_cooler"
    RAM_KIT = "ram_kit"
    STORAGE_DRIVE = "storage_drive"
    PSU = "psu"
    CASE = "case"
    FAN = "fan"


# Categories whose approval creates a pc_parts row plus (or reusing) a *_groups
# row. Discovery always targets the purchasable SKU — that is what a spec page
# is written about and what a buyer searches for — and the group's intrinsic
# spec is extracted alongside it, so approval can find-or-create the group.
GROUPED_CATEGORIES = frozenset({"ram_kit", "storage_drive", "psu"})

# Categories that do not produce a pc_parts row at all.
NON_PART_CATEGORIES = frozenset({"gpu_chipset", "ai_model"})


class MatchMethod(str, enum.Enum):
    EXACT = "exact"
    MODEL_NUMBER = "model_number"
    FUZZY = "fuzzy"
    EMBEDDING = "embedding"  # reserved for a future pgvector tier; never produced in v1


class ValidationStatus(str, enum.Enum):
    PASSED = "passed"
    FAILED = "failed"


class ReviewStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"


class DiscoveryRun(Base):
    __tablename__ = "discovery_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    run_type = Column(Text, nullable=False)
    status = Column(
        Text, nullable=False, server_default=DiscoveryRunStatus.RUNNING.value
    )

    # Same git-SHA resolution as the dspy pipeline, so extraction quality can be
    # cohorted by code version when reviewing the queue.
    pipeline_version = Column(Text, nullable=False)
    model_name = Column(Text, nullable=False)

    sources_checked = Column(Integer, nullable=False, server_default="0")
    items_found = Column(Integer, nullable=False, server_default="0")
    items_new = Column(Integer, nullable=False, server_default="0")

    error_detail = Column(Text, nullable=True)

    total_cost_usd = Column(Numeric(10, 6), nullable=True)
    tokens_in = Column(Integer, nullable=True)
    tokens_out = Column(Integer, nullable=True)

    started_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at = Column(DateTime(timezone=True), nullable=True)

    items = relationship(
        "DiscoveredItem",
        back_populates="run",
        cascade="all, delete-orphan",
    )


class DiscoverySource(Base):
    """Seed pages monitored by the scheduled discovery job. Unused by the
    on-demand path; rows carry the content hash that lets the monthly run skip
    unchanged pages."""

    __tablename__ = "discovery_sources"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    url = Column(Text, nullable=False)
    category = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, server_default="true")

    last_fetched_at = Column(DateTime(timezone=True), nullable=True)
    last_content_hash = Column(Text, nullable=True)


class DiscoveredItem(Base):
    __tablename__ = "discovered_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("discovery_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    category = Column(Text, nullable=False)
    name_normalized = Column(Text, nullable=False)
    model_number = Column(Text, nullable=True)

    # Keys are exactly pc_parts + subtype column names (plus `chipset_name` for
    # gpu_variant), so approval can cast this straight into the insert.
    extracted_fields = Column(JSONB, nullable=False)
    # {field: {"source_url": ..., "snippet": ...}} — kept separate from the
    # payload so approval doesn't have to strip provenance back out.
    field_provenance = Column(JSONB, nullable=False)
    # Multi-source disagreement flags: {field: {"agreement": ..., "n_sources": ...,
    # "values": {url: value, ...}}} — "values" present only where sources disagree.
    extraction_confidence = Column(JSONB, nullable=True)
    source_urls = Column(ARRAY(Text), nullable=False)

    # Dedup result. gpu_chipset matches land on matched_chipset_id because
    # chipsets are groups, not pc_parts rows; every other category uses
    # matched_part_id.
    matched_part_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pc_parts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    matched_chipset_id = Column(
        UUID(as_uuid=True),
        ForeignKey("gpu_chipsets.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # ai_models is a standalone catalog, not a pc_parts subtype, so it needs its
    # own match/audit columns rather than reusing matched_part_id.
    matched_ai_model_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ai_models.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    match_method = Column(Text, nullable=True)
    match_score = Column(Float, nullable=True)

    validation_status = Column(Text, nullable=False)
    validation_errors = Column(JSONB, nullable=True)

    review_status = Column(
        Text, nullable=False, server_default=ReviewStatus.PENDING.value
    )
    reviewed_at = Column(DateTime(timezone=True), nullable=True)

    # Audit links set on approval (chipset counterpart for gpu_chipset rows).
    created_part_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pc_parts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_chipset_id = Column(
        UUID(as_uuid=True),
        ForeignKey("gpu_chipsets.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_ai_model_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ai_models.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    run = relationship("DiscoveryRun", back_populates="items")

    __table_args__ = (
        # A re-run of the same item refreshes its pending row (ON CONFLICT DO
        # UPDATE in crud) instead of stacking duplicates; reviewed rows fall
        # outside the predicate so re-discovery creates a fresh pending row.
        Index(
            "uq_discovered_items_pending_cat_name",
            "category",
            "name_normalized",
            unique=True,
            postgresql_where=text("review_status = 'pending'"),
        ),
    )
