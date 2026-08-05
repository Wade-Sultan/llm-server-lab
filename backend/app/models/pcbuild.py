import enum
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
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

# Roles a build may fill more than once. Everything else is a singleton: a
# build has one CPU, one board, one PSU, one case, and one cooler, and that is
# still enforced in the database (see BuildPart.__table_args__).
#
# These three are not arbitrary. A machine built to serve LLMs is defined by
# hosting several GPUs; storage is routinely split (a fast NVMe boot drive plus
# bulk capacity for weights and datasets); and fans are bought per case slot.
# RAM is deliberately NOT here — a memory kit is already a multi-module product
# (ram_groups.modules), so a second RAM row would double-count rather than
# describe a second thing.
MULTI_INSTANCE_ROLES = frozenset(
    {
        BuildComponentRole.GPU,
        BuildComponentRole.STORAGE,
        BuildComponentRole.FAN,
    }
)

# SQL predicates derived from the set above so the constraints can never drift
# from it. Sorted for a stable DDL string — an unsorted frozenset would make
# the generated predicate vary between processes and look like schema drift.
_MULTI_ROLE_VALUES = ", ".join(sorted(f"'{r.value}'" for r in MULTI_INSTANCE_ROLES))
IS_MULTI_INSTANCE_ROLE_SQL = f"role IN ({_MULTI_ROLE_VALUES})"
IS_SINGLETON_ROLE_SQL = f"role NOT IN ({_MULTI_ROLE_VALUES})"


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
        doc="Sum of all part LINE totals after the pricing pipeline runs — i.e. "
        "price_at_build * quantity per row, not a sum of unit prices (see "
        "BuildPart.line_total_cents)",
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
    Association table between PCBuild and PCPart. One row per *distinct* part
    in a role; identical units are one row with quantity > 1.

    - role: which "slot" this part fills (cpu, gpu, etc.)
    - required_component: whether this slot must be present for a valid build
      (e.g. CPU=True, GPU=False)
    - quantity: how many of this part the build uses

    Roles in MULTI_INSTANCE_ROLES may appear several times; every other role is
    still exactly one row per build. The two representations of "more than one"
    are kept unambiguous by the constraints below: two different GPUs are two
    rows, two identical GPUs are one row with quantity 2, and the same part can
    never be listed twice in the same role.
    """

    __tablename__ = "pc_build_parts"

    __table_args__ = (
        # Replaces the old table-wide UniqueConstraint on (build_id, role).
        # Singleton roles keep exactly the guarantee they had; the multi-instance
        # roles fall outside the predicate and may repeat.
        Index(
            "uq_pc_build_parts_singleton_role",
            "build_id",
            "role",
            unique=True,
            postgresql_where=text(IS_SINGLETON_ROLE_SQL),
        ),
        # Forces the quantity column to be the only way to say "two of these",
        # so a build can't carry the same drive as both one row of 2 and two
        # rows of 1. Restricted to non-null part_id because Postgres treats
        # NULLs as distinct, and an unfilled slot is not a duplicate of another
        # unfilled slot.
        Index(
            "uq_pc_build_parts_role_part",
            "build_id",
            "role",
            "part_id",
            unique=True,
            postgresql_where=text("part_id IS NOT NULL"),
        ),
        CheckConstraint("quantity >= 1", name="ck_pc_build_parts_quantity_positive"),
        # A second CPU is not expressible as quantity=2 either. Enforced here
        # rather than in the ORM: SQLAlchemy validators fire per-attribute in
        # assignment order, so a check that needs both role and quantity can't
        # be relied on to see both.
        CheckConstraint(
            f"quantity = 1 OR {IS_MULTI_INSTANCE_ROLE_SQL}",
            name="ck_pc_build_parts_singleton_quantity",
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

    quantity = Column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
        doc="How many of this part the build uses. Only ever >1 for roles in "
        "MULTI_INSTANCE_ROLES (four identical GPUs, three case fans).",
    )

    price_at_build = Column(
        Integer,
        nullable=True,
        doc="PER-UNIT part price in local cents at the time this build was "
        "finalized. The line total is price_at_build * quantity — see "
        "line_total_cents. Per-unit rather than per-line so it stays "
        "comparable with pc_parts.street_price_cents, which is what it is "
        "snapshotted from.",
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

    @property
    def line_total_cents(self) -> int | None:
        """What this row contributes to the build total. Exists so callers
        summing a build can't quietly forget the quantity multiplier — the
        failure mode is a build that under-reports its own price."""
        if self.price_at_build is None:
            return None
        return self.price_at_build * (self.quantity or 1)

    @validates("role")
    def _set_required_component_default(self, _key, role):
        if self.required_component is None:
            self.required_component = REQUIRED_COMPONENT_BY_ROLE.get(role, False)
        return role
