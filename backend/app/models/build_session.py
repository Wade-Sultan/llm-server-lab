"""
build_session.py
================
Telemetry tables for the DSPy recommender pipeline.

`build_sessions` — one row per pipeline run (one build conversation).
`module_decisions` — one row per `Decide*` module call. This is the row that
later becomes a GEPA training example, so it snapshots *verbatim* what the LLM
saw at decision time (candidate set, input state, prompt hash). See
app/services/recommender/recording.py for how these are populated.
"""

import enum
import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base


class BuildSessionStatus(str, enum.Enum):
    RUNNING = "running"        # Pipeline started, not yet finished
    COMPLETED = "completed"    # Pipeline finished all steps
    ABANDONED = "abandoned"    # User left before the run resumed/finished
    ERROR = "error"            # Pipeline raised


class BuildSession(Base):
    """One row per DSPy recommender run. GEPA cohort is keyed by pipeline_version."""

    __tablename__ = "build_sessions"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
    )

    # Nullable — anonymous builds must still be usable as training data.
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
        index=True,
    )

    # Git commit / semver of the DSPy pipeline code. GEPA cohort key.
    pipeline_version = Column(Text, nullable=False)

    budget_cents = Column(Integer, nullable=True)
    price_sensitivity = Column(
        Text,
        nullable=True,
        doc="Not yet emitted by BuildRequest; reserved for the future field",
    )

    input_profile = Column(
        JSONB,
        nullable=False,
        doc="Full BuildRequest as submitted, before any decisions",
    )
    final_build = Column(
        JSONB,
        nullable=True,
        doc="Denormalized {category: part_id} of the final build for dashboard reads",
    )

    # When the DSPy build succeeds, the customer is shown the DSPy build but the
    # reference build that was resolved in parallel is still recorded here, so
    # every successful run carries a DSPy-vs-reference comparison pair.
    reference_build_key = Column(
        Text,
        nullable=True,
        doc="Key of the reference build resolved alongside this DSPy run",
    )
    reference_build = Column(
        JSONB,
        nullable=True,
        doc="Full resolved reference Build payload (label, parts, total) at run time",
    )

    total_cost_usd = Column(
        Numeric,
        nullable=True,
        doc="Sum of LLM API cost across all module calls",
    )
    total_latency_ms = Column(Integer, nullable=True)

    budget_delta_cents = Column(
        Integer,
        nullable=True,
        doc="Final build price - budget. Cheapest available reward signal",
    )
    compatibility_override_count = Column(
        Integer,
        nullable=False,
        server_default="0",
        doc="How many decisions picked a part outside the candidate set",
    )

    status = Column(
        Enum(
            BuildSessionStatus,
            name="build_session_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        server_default="running",
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    decisions = relationship(
        "ModuleDecision",
        back_populates="session",
        cascade="all, delete-orphan",
    )


class ModuleDecision(Base):
    """
    One row per `Decide*` call. The GEPA training example.

    candidate_set and input_state are stored as raw JSONB snapshots — NOT
    references — so a replay months later sees exactly what the model saw, even
    after pc_parts pricing/inventory drifts.
    """

    __tablename__ = "module_decisions"

    __table_args__ = (
        # Primary GEPA extraction shape: all decisions for one module, one
        # pipeline era, windowed by time. pipeline_version is denormalized onto
        # this table (copied from the session) so the query needs no join.
        Index(
            "ix_module_decisions_category_pipeline_created",
            "category",
            "pipeline_version",
            "created_at",
        ),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
    )

    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("build_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Denormalized from the parent session for the composite extraction index.
    pipeline_version = Column(Text, nullable=False)

    category = Column(String(32), nullable=False, doc="cpu, gpu, psu, …")
    sequence_order = Column(
        Integer,
        nullable=False,
        doc="Position in dependency order (DDR=0 … Fans=9)",
    )

    signature_name = Column(String(64), nullable=False, doc="e.g. DecideGPU")
    signature_version = Column(
        Integer,
        nullable=False,
        doc="Bumped manually when the signature's input/output fields change "
            "shape. Independent of pipeline_version",
    )

    candidate_set = Column(
        JSONB,
        nullable=True,
        doc="Verbatim candidates the LLM saw: specs + price at decision time",
    )
    input_state = Column(
        JSONB,
        nullable=True,
        doc="Non-candidate inputs to this call (profile/prior selections snapshot)",
    )
    raw_prompt_hash = Column(
        String(64),
        nullable=True,
        doc="sha256 of the rendered prompt — detects drift without storing text",
    )
    output_decision = Column(
        JSONB,
        nullable=True,
        doc="Chosen part_id/name/tier plus reasoning fields the signature emits",
    )

    tokens_in = Column(Integer, nullable=True)
    tokens_out = Column(Integer, nullable=True)
    cost_usd = Column(Numeric, nullable=True)
    latency_ms = Column(Integer, nullable=True)

    was_override = Column(
        Boolean,
        nullable=False,
        server_default="false",
        doc="Chosen part was not present in candidate_set (out-of-set pick)",
    )

    model_name = Column(
        String(128),
        nullable=True,
        doc="Resolved from the active DSPy LM — haiku vs Gemma comparisons",
    )

    outcome_signal = Column(
        JSONB,
        nullable=True,
        doc="Backfilled later: thumbs feedback, clicked, survived-to-final-build",
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    session = relationship("BuildSession", back_populates="decisions")
