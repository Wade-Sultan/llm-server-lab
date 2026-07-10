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
from sqlalchemy.dialects.postgresql import UUID
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
        index=True
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
    reached_recommendation = Column(
        Boolean,
        nullable=False,
        server_default="false",
        doc="True once this conversation produced a recommended build — used to "
            "distinguish 'cost per completed build' from 'cost per chat'",
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
    