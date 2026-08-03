import enum
import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import relationship, validates
from sqlalchemy.sql import func

from app.db.base import Base


class BuildComponentRole(str, enum.Enum):
    CPU = "cpu"
    CPU_COOLER = "cpucooler"
    MOTHERBOARD = "motherboard"
    RAM = "ram"
    STORAGE = "storage"
    GPU = "gpu"
    PSU = "psu"
    CASE = "case"
    FAN = "fan"


class BuildStatus(str, enum.Enum):
    DRAFT = "draft"  # User is still in the configurator
    RECOMMENDED = "recommended"  # Pipeline finished — parts selected
    PRICED = "priced"  # Amazon pricing pipeline has run
    FINALIZED = "finalized"  # User confirmed the build
    ORDERED = "ordered"  # Parts purchased (future)


REQUIRED_COMPONENT_BY_ROLE = {
    BuildComponentRole.CPU: True,
    BuildComponentRole.CPU_COOLER: True,
    BuildComponentRole.MOTHERBOARD: True,
    BuildComponentRole.RAM: True,
    BuildComponentRole.STORAGE: True,
    BuildComponentRole.GPU: False,
    BuildComponentRole.PSU: True,
    BuildComponentRole.CASE: True,
    BuildComponentRole.FAN: False,
}


class PCBuild(Base):
    __tablename__ = "pc_builds"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
    )

    name = Column(String(255), nullable=False, server_default="Untitled Build")
    description = Column(Text, nullable=True)

    owner_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True
    )

    status = Column(
        Enum(
            BuildStatus,
            name="build_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        server_default="draft",
    )

    total_price_cents = Column(
        Integer,
        nullable=True,
        doc="Sum of all part prices after pricing pipeline runs",
    )

    use_cases = Column(
        ARRAY(String),
        nullable=True,
        doc="e.g. ['gaming', 'streaming']",
    )
    preferences = Column(
        JSONB,
        nullable=True,
        doc="Snapshot of UserPreferences dict from the configurator",
    )
    questionnaire_answers = Column(
        JSONB,
        nullable=True,
        doc="Snapshot of the flat answers dict from the configurator",
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

    parts = relationship(
        "BuildPart",
        back_populates="build",
        cascade="all, delete-orphan",
    )


class BuildPart(Base):
    """
    Association table between PCBuild and PCPart.

    - role: which "slot" this part fills (cpu, gpu, etc.)
    - required_component: whether this slot must be present for a valid build
      (e.g. CPU=True, GPU=False)
    """

    __tablename__ = "pc_build_parts"

    __table_args__ = (
        UniqueConstraint(
            "build_id",
            "role",
            name="uq_pc_build_parts_build_role",
        ),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
    )

    build_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pc_builds.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    part_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pc_parts.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    role = Column(
        Enum(
            BuildComponentRole,
            name="build_component_role",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )

    required_component = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    price_at_build = Column(
        Integer,
        nullable=True,
        doc="Part price in local cents at the time this build was finalized",
    )

    selection_reason = Column(
        Text,
        nullable=True,
        doc="Short rationale from the recommender pipeline",
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    build = relationship("PCBuild", back_populates="parts")
    part = relationship("PCPart")

    @validates("role")
    def _set_required_component_default(self, _key, role):
        if self.required_component is None:
            self.required_component = REQUIRED_COMPONENT_BY_ROLE.get(role, False)
        return role
