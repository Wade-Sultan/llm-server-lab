import enum
import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base


class SoftwareCategory(str, enum.Enum):
    CREATIVE = "creative"  # Premiere, Resolve, Blender, etc.
    PRODUCTIVITY = "productivity"  # Office, browsers, IDEs
    AI_ML = "ai_ml"  # PyTorch, TensorFlow, llama.cpp
    DEVELOPMENT = "development"  # Compilers, Docker, local LLMs
    STREAMING = "streaming"  # OBS, vMix


class GpuImportance(str, enum.Enum):
    """How much a discrete GPU matters for a given software + tier."""

    REQUIRED = "required"  # Won't run / unusable without one
    ACCELERATED = "accelerated"  # Runs on CPU but massively benefits from GPU
    OPTIONAL = "optional"  # Minor benefit at best
    IRRELEVANT = "irrelevant"  # Pure CPU workload


class Software(Base):
    __tablename__ = "software"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
    )

    name = Column(String(255), nullable=False, unique=True)
    slug = Column(String(255), nullable=False, unique=True)  # e.g. "davinci-resolve"

    category = Column(String(30), nullable=False)

    # Per-software use cases
    use_case_tags = Column(ARRAY(String), nullable=False, server_default="{}")

    developer = Column(String(255), nullable=True)
    current_version = Column(String(50), nullable=True)
    website_url = Column(Text, nullable=True)
    image_url = Column(Text, nullable=True)

    is_free = Column(Boolean, nullable=True)

    platform_requirements = Column(ARRAY(String), nullable=True)

    notes = Column(Text, nullable=True)

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

    tiers = relationship(
        "SoftwareTier",
        back_populates="software",
        cascade="all, delete-orphan",
        order_by="SoftwareTier.sort_order",
    )


class SoftwareTier(Base):
    __tablename__ = "software_tiers"

    __table_args__ = (
        UniqueConstraint(
            "software_id",
            "slug",
            name="uq_software_tiers_software_slug",
        ),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
    )

    software_id = Column(
        UUID(as_uuid=True),
        ForeignKey("software.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name = Column(String(100), nullable=False)  # "4K editing"
    slug = Column(String(100), nullable=False)  # "4k-editing"
    sort_order = Column(Integer, nullable=False, default=0)

    # Hardware guidance

    gpu_importance = Column(
        String(20),
        nullable=False,
        default=GpuImportance.OPTIONAL.value,
        server_default=GpuImportance.OPTIONAL.value,
    )

    min_ram_gb = Column(Integer, nullable=True)
    recommended_ram_gb = Column(Integer, nullable=True)
    min_vram_gb = Column(Integer, nullable=True)
    min_storage_gb = Column(Integer, nullable=True)

    # CPU-side: core count / thread count guidance
    min_cores = Column(Integer, nullable=True)
    prefers_single_thread = Column(
        Boolean,
        nullable=True,
        comment="True if workload is latency-bound (IDE, light tasks); "
        "False/NULL if it scales with core count",
    )

    extra_requirements = Column(JSONB, nullable=True)

    notes = Column(Text, nullable=True)

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

    software = relationship("Software", back_populates="tiers")
    minimum_parts = relationship(
        "SoftwareMinimumPart",
        back_populates="tier",
        cascade="all, delete-orphan",
    )


class SoftwareMinimumPart(Base):
    __tablename__ = "software_minimum_parts"

    __table_args__ = (
        UniqueConstraint(
            "tier_id",
            "role",
            name="uq_software_min_parts_tier_role",
        ),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
    )

    tier_id = Column(
        UUID(as_uuid=True),
        ForeignKey("software_tiers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # cpu or gpu
    role = Column(String(20), nullable=False)

    part_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pc_parts.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    published_name = Column(String(255), nullable=True)

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

    tier = relationship("SoftwareTier", back_populates="minimum_parts")
    part = relationship("PCPart")
