"""add server-platform columns and AI-model discovery links

Two things the "server" use case needs that the consumer-desktop schema never
had to carry:

1. Server-platform spec. What separates a Threadripper/Xeon/EPYC build from a
   consumer one is not clock speed, it is PCIe lanes (how many GPUs and NVMe
   drives can run at full width), memory channels (bandwidth, which is the
   binding constraint for HPC and CPU-side inference), and ECC/registered
   memory. None of those were expressible, so the recommender could pick a
   Threadripper by name but could not reason about why anyone would want one.

2. AI-model discovery links. discovered_items could only point at pc_parts and
   gpu_chipsets. ai_models is neither, so an approved AI model had nowhere to
   record its dedup match or its audit link back to the created row.

Revision ID: a2b4c6d8e0f1
Revises: a1b2c3d4e5f6
Create Date: 2026-08-04 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "a2b4c6d8e0f1"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    # --- CPU: the three axes that actually define a workstation/server part ---
    # pcie_lanes is CPU-provided lanes (Threadripper 7970X = 88, 9800X3D = 28).
    # It is the hard ceiling on multi-GPU: four x16 GPUs need 64 lanes from the
    # CPU no matter what the board advertises.
    op.add_column("cpus", sa.Column("pcie_lanes", sa.Integer(), nullable=True))
    # Memory channels drive bandwidth, which is what CPU-side LLM inference and
    # most HPC codes are actually limited by (8-channel TR PRO ≈ 4x a 2-channel
    # desktop part at the same DDR speed).
    op.add_column("cpus", sa.Column("memory_channels", sa.Integer(), nullable=True))
    # Nullable, not server_default false: "unknown" and "does not support ECC"
    # are different claims, and a consumer part discovered without the field
    # stated should not be recorded as a definitive no.
    op.add_column("cpus", sa.Column("supports_ecc", sa.Boolean(), nullable=True))

    # --- Motherboard: the board half of the same story ---
    op.add_column("motherboards", sa.Column("supports_ecc", sa.Boolean(), nullable=True))
    # Out-of-band management (IPMI/BMC) — the single clearest signal that a board
    # is a server board rather than a workstation board.
    op.add_column("motherboards", sa.Column("has_ipmi", sa.Boolean(), nullable=True))
    # Server boards routinely carry 8 DIMM slots across 4-8 channels; memory_slots
    # alone cannot tell the recommender how to populate them for full bandwidth.
    op.add_column(
        "motherboards", sa.Column("memory_channels", sa.Integer(), nullable=True)
    )
    # Which DIMM types the board actually accepts. This is the compatibility
    # half of ram_groups.module_type below, and it is what stops the RAM step
    # from pairing a TRX50/WRX90 board with unbuffered kits it will not POST
    # on. NULL means "unconstrained", so every existing consumer board keeps
    # seeing the same candidate set it does today.
    op.add_column(
        "motherboards",
        sa.Column("memory_module_types", ARRAY(sa.String()), nullable=True),
    )

    # --- RAM: registered vs unbuffered is a hard compatibility wall ---
    # is_ecc already existed but is not sufficient: Threadripper PRO and EPYC
    # require RDIMMs and will not POST on unbuffered ECC UDIMMs, while consumer
    # AM5 boards accept ECC UDIMMs and reject RDIMMs. One boolean cannot express
    # a three-way incompatibility, so store the module type.
    op.add_column("ram_groups", sa.Column("module_type", sa.String(10), nullable=True))
    # Existing rows are all consumer kits; udimm is the correct backfill.
    op.execute("UPDATE ram_groups SET module_type = 'udimm' WHERE module_type IS NULL")

    # --- discovered_items: ai_models is not a pc_parts subtype ---
    op.add_column(
        "discovered_items",
        sa.Column(
            "matched_ai_model_id",
            UUID(as_uuid=True),
            sa.ForeignKey("ai_models.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "discovered_items",
        sa.Column(
            "created_ai_model_id",
            UUID(as_uuid=True),
            sa.ForeignKey("ai_models.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_discovered_items_matched_ai_model_id",
        "discovered_items",
        ["matched_ai_model_id"],
    )
    op.create_index(
        "ix_discovered_items_created_ai_model_id",
        "discovered_items",
        ["created_ai_model_id"],
    )


def downgrade():
    op.drop_index(
        "ix_discovered_items_created_ai_model_id", table_name="discovered_items"
    )
    op.drop_index(
        "ix_discovered_items_matched_ai_model_id", table_name="discovered_items"
    )
    op.drop_column("discovered_items", "created_ai_model_id")
    op.drop_column("discovered_items", "matched_ai_model_id")

    op.drop_column("ram_groups", "module_type")

    op.drop_column("motherboards", "memory_module_types")
    op.drop_column("motherboards", "memory_channels")
    op.drop_column("motherboards", "has_ipmi")
    op.drop_column("motherboards", "supports_ecc")

    op.drop_column("cpus", "supports_ecc")
    op.drop_column("cpus", "memory_channels")
    op.drop_column("cpus", "pcie_lanes")
