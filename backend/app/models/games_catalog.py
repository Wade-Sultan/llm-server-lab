import enum
import uuid

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base


class RequirementTier(str, enum.Enum):
    MINIMUM = "minimum"
    RECOMMENDED = "recommended"
    ULTRA = "ultra"


class Game(Base):
    __tablename__ = "games"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
    )

    title = Column(String(255), nullable=False, unique=True)
    slug = Column(String(255), nullable=False, unique=True)  # e.g. "cyberpunk-2077"

    genre = Column(
        String(50), nullable=True
    )  # e.g. "aaa_open_world", "competitive_fps"
    store_url = Column(Text, nullable=True)  # Steam, Epic, etc.
    image_url = Column(Text, nullable=True)

    # This is to record features that are absolutely necessary for the title to run at all
    hard_requirements = Column(ARRAY(String), nullable=True)

    min_storage_gb = Column(Integer, nullable=True)

    # Publisher's notes
    requirements_notes = Column(Text, nullable=True)

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

    minimum_parts = relationship(
        "GameMinimumPart",
        back_populates="game",
        cascade="all, delete-orphan",
    )


class GameMinimumPart(Base):
    __tablename__ = "game_minimum_parts"

    __table_args__ = (
        UniqueConstraint(
            "game_id",
            "tier",
            "role",
            name="uq_game_min_parts_game_tier_role",
        ),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
    )

    game_id = Column(
        UUID(as_uuid=True),
        ForeignKey("games.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Spec level
    tier = Column(String(20), nullable=False)

    # CPU or GPU
    role = Column(String(20), nullable=False)

    part_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pc_parts.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    # The marketed name for the part
    published_name = Column(String(255), nullable=True)

    min_ram_gb = Column(Integer, nullable=True)

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

    game = relationship("Game", back_populates="minimum_parts")
    part = relationship("PCPart")
