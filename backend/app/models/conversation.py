import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
    )

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    build_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pc_builds.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    title = Column(String(255), nullable=True)

    summary = Column(Text, nullable=True)

    # Running totals of all OpenRouter LLM spend across this conversation's turns
    # (profile extraction, elicitation, recommendation). Incremented per turn.
    total_cost_usd = Column(
        Numeric,
        nullable=False,
        server_default="0",
        doc="Sum of LLM API cost across every turn of this conversation",
    )
    total_tokens_in = Column(Integer, nullable=False, server_default="0")
    total_tokens_out = Column(Integer, nullable=False, server_default="0")
    llm_call_count = Column(
        Integer,
        nullable=False,
        server_default="0",
        doc="Number of LLM calls billed to this conversation",
    )
    models_used = Column(
        ARRAY(String),
        nullable=False,
        server_default="{}",
        doc="Distinct OpenRouter model names used across this conversation's turns "
        "(extraction, elicitation, recommendation)",
    )
    reached_recommendation = Column(
        Boolean,
        nullable=False,
        server_default="false",
        doc="True once this conversation produced a recommended build — used to "
        "distinguish 'cost per completed build' from 'cost per chat'",
    )

    # Resolved once (either as the budget-still-unknown price estimate, or
    # alongside a completed DSPy run) and never re-resolved afterward — the
    # guaranteed, free-to-fetch reference build for the rest of this
    # conversation, including as the DSPy-failure fallback. Mirrors
    # BuildSession.reference_build_key/reference_build.
    reference_build_key = Column(
        Text,
        nullable=True,
        doc="Key of the reference build cached for this conversation",
    )
    reference_build = Column(
        JSONB,
        nullable=True,
        doc="Full resolved reference Build payload (label, parts, total) cached for this conversation",
    )

    # Write-behind mirror of the LangGraph checkpointer's latest checkpoint for
    # this conversation, written in the same transaction as the turn itself.
    # Valkey is the hot store and the only one that keeps pending writes; this
    # is what survives GRAPH_CHECKPOINT_TTL_S expiring or Memorystore being
    # flushed, so a conversation resumed days later still knows the user's
    # accumulated profile instead of re-asking every question.
    # See app/services/graph/checkpoint.py.
    graph_checkpoint = Column(
        JSONB,
        nullable=True,
        doc="Serialized latest LangGraph checkpoint (base64 payload in a JSON envelope)",
    )
    graph_checkpoint_id = Column(
        Text,
        nullable=True,
        doc="Id of the checkpoint held in graph_checkpoint, for correlating with Valkey",
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

    user = relationship(
        "User",
        back_populates="conversations",
    )

    messages = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )
