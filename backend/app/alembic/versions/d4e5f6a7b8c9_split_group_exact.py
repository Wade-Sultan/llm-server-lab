"""split GPU/PSU/RAM/Storage into group + exact tables

Creates the four standalone group tables (gpu_chipsets, psu_groups, ram_groups,
storage_groups), backfills them by deduping the existing flat exact rows,
repoints each exact at its group via a NOT NULL FK, drops the moved intrinsic
columns from the exacts, and renames ram→ram_kits / storage→storage_drives
(with their pc_parts.part_type identities).

Revision ID: d4e5f6a7b8c9
Revises: f7b3c2a9d4e1
Create Date: 2026-07-13 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "d4e5f6a7b8c9"
down_revision = "f7b3c2a9d4e1"
branch_labels = None
depends_on = None


def _timestamps():
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ------------------------------------------------------------------
    # 1) Group tables
    # ------------------------------------------------------------------
    op.create_table(
        "gpu_chipsets",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("vram_gb", sa.Integer(), nullable=False),
        sa.Column("vram_type", sa.String(length=20), nullable=True),
        sa.Column("tdp_watts", sa.Integer(), nullable=False),
        sa.Column("recommended_psu_watts", sa.Integer(), nullable=True),
        sa.Column("pcie_generation", sa.Integer(), nullable=True),
        sa.Column("base_clock_mhz", sa.Integer(), nullable=True),
        sa.Column("boost_clock_mhz", sa.Integer(), nullable=True),
        sa.Column("has_ray_tracing", sa.Boolean(), nullable=True),
        sa.Column("cuda_cores", sa.Integer(), nullable=True),
        sa.Column("tensor_cores", sa.Integer(), nullable=True),
        sa.Column("stream_processors", sa.Integer(), nullable=True),
        sa.Column("matrix_cores", sa.Integer(), nullable=True),
        sa.Column("supported_features", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("benchmark_scores", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_gpu_chipsets_id"), "gpu_chipsets", ["id"], unique=False)

    op.create_table(
        "psu_groups",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("wattage", sa.Integer(), nullable=False),
        sa.Column("form_factor", sa.String(length=10), nullable=False),
        sa.Column("efficiency_rating", sa.String(length=30), nullable=False),
        sa.Column("modular", sa.String(length=10), nullable=True),
        sa.Column("is_fanless", sa.Boolean(), nullable=True),
        sa.Column("fan_size_mm", sa.Integer(), nullable=True),
        sa.Column("pcie_8pin_connectors", sa.Integer(), nullable=True),
        sa.Column("pcie_12pin_connectors", sa.Integer(), nullable=True),
        sa.Column("pcie_16pin_connectors", sa.Integer(), nullable=True),
        sa.Column("eps_connectors", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_psu_groups_id"), "psu_groups", ["id"], unique=False)

    op.create_table(
        "ram_groups",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("ddr_generation", sa.String(length=10), nullable=False),
        sa.Column("speed_mhz", sa.Integer(), nullable=False),
        sa.Column("capacity_gb", sa.Integer(), nullable=False),
        sa.Column("modules", sa.Integer(), nullable=False),
        sa.Column("module_capacity_gb", sa.Integer(), nullable=True),
        sa.Column("cas_latency", sa.Integer(), nullable=True),
        sa.Column("voltage", sa.Float(), nullable=True),
        sa.Column("is_ecc", sa.Boolean(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ram_groups_id"), "ram_groups", ["id"], unique=False)

    op.create_table(
        "storage_groups",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("storage_type", sa.String(length=20), nullable=False),
        sa.Column("form_factor", sa.String(length=20), nullable=False),
        sa.Column("interface", sa.String(length=20), nullable=False),
        sa.Column("capacity_gb", sa.Integer(), nullable=False),
        sa.Column("read_speed_mbps", sa.Integer(), nullable=True),
        sa.Column("write_speed_mbps", sa.Integer(), nullable=True),
        sa.Column("has_dram_cache", sa.Boolean(), nullable=True),
        sa.Column("endurance_tbw", sa.Integer(), nullable=True),
        sa.Column("rpm", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_storage_groups_id"), "storage_groups", ["id"], unique=False)

    # ------------------------------------------------------------------
    # 2) Backfill groups (dedupe the flat exact rows by their spec key) and
    #    3) repoint each exact at its group via a NOT NULL FK.
    # ------------------------------------------------------------------

    # --- GPU: one chipset per distinct chipset name ---
    # DISTINCT ON picks one representative row per group key, so array/JSONB
    # columns (supported_features, benchmark_scores) copy through unchanged —
    # aggregating them (array_agg(...)[1]) collapses an array column to a scalar.
    op.execute(
        """
        INSERT INTO gpu_chipsets
            (id, name, vram_gb, vram_type, tdp_watts, recommended_psu_watts, pcie_generation,
             base_clock_mhz, boost_clock_mhz, has_ray_tracing, cuda_cores, tensor_cores,
             stream_processors, matrix_cores, supported_features, benchmark_scores)
        SELECT DISTINCT ON (chipset)
               gen_random_uuid(), chipset, vram_gb, vram_type, tdp_watts, recommended_psu_watts,
               pcie_generation, base_clock_mhz, boost_clock_mhz, has_ray_tracing, cuda_cores,
               tensor_cores, stream_processors, matrix_cores, supported_features, benchmark_scores
        FROM gpus ORDER BY chipset, id
        """
    )
    op.add_column("gpus", sa.Column("gpu_chipset_id", sa.UUID(), nullable=True))
    op.execute("UPDATE gpus g SET gpu_chipset_id = c.id FROM gpu_chipsets c WHERE c.name = g.chipset")
    op.alter_column("gpus", "gpu_chipset_id", nullable=False)
    op.create_index(op.f("ix_gpus_gpu_chipset_id"), "gpus", ["gpu_chipset_id"], unique=False)
    op.create_foreign_key(
        "fk_gpus_gpu_chipset_id", "gpus", "gpu_chipsets", ["gpu_chipset_id"], ["id"], ondelete="RESTRICT"
    )
    for col in (
        "chipset", "vram_gb", "tdp_watts", "recommended_psu_watts", "supported_features",
        "benchmark_scores", "vram_type", "pcie_generation", "base_clock_mhz", "boost_clock_mhz",
        "has_ray_tracing", "cuda_cores", "tensor_cores", "stream_processors", "matrix_cores",
    ):
        op.drop_column("gpus", col)

    # --- PSU: one group per (wattage, efficiency, form_factor) ---
    op.execute(
        """
        INSERT INTO psu_groups
            (id, name, wattage, form_factor, efficiency_rating, modular, is_fanless, fan_size_mm,
             pcie_8pin_connectors, pcie_12pin_connectors, pcie_16pin_connectors, eps_connectors)
        SELECT DISTINCT ON (wattage, efficiency_rating, form_factor)
               gen_random_uuid(),
               wattage || 'W ' || efficiency_rating || ' ' || form_factor,
               wattage, form_factor, efficiency_rating, modular, is_fanless, fan_size_mm,
               pcie_8pin_connectors, pcie_12pin_connectors, pcie_16pin_connectors, eps_connectors
        FROM psus ORDER BY wattage, efficiency_rating, form_factor, id
        """
    )
    op.add_column("psus", sa.Column("psu_group_id", sa.UUID(), nullable=True))
    op.execute(
        """
        UPDATE psus p SET psu_group_id = g.id FROM psu_groups g
        WHERE g.wattage = p.wattage AND g.efficiency_rating = p.efficiency_rating
          AND g.form_factor = p.form_factor
        """
    )
    op.alter_column("psus", "psu_group_id", nullable=False)
    op.create_index(op.f("ix_psus_psu_group_id"), "psus", ["psu_group_id"], unique=False)
    op.create_foreign_key(
        "fk_psus_psu_group_id", "psus", "psu_groups", ["psu_group_id"], ["id"], ondelete="RESTRICT"
    )
    for col in (
        "wattage", "form_factor", "efficiency_rating", "pcie_8pin_connectors",
        "pcie_12pin_connectors", "pcie_16pin_connectors", "modular", "eps_connectors",
        "fan_size_mm", "is_fanless",
    ):
        op.drop_column("psus", col)

    # --- RAM: one group per (ddr, speed, capacity, modules); then rename table ---
    op.execute(
        """
        INSERT INTO ram_groups
            (id, name, ddr_generation, speed_mhz, capacity_gb, modules, module_capacity_gb,
             cas_latency, voltage, is_ecc)
        SELECT DISTINCT ON (ddr_generation, speed_mhz, capacity_gb, modules)
               gen_random_uuid(),
               upper(ddr_generation) || '-' || speed_mhz || ' ' || capacity_gb || 'GB (' ||
                   modules || 'x' || (capacity_gb / GREATEST(modules, 1)) || ')',
               ddr_generation, speed_mhz, capacity_gb, modules, module_capacity_gb,
               cas_latency, voltage, is_ecc
        FROM ram ORDER BY ddr_generation, speed_mhz, capacity_gb, modules, id
        """
    )
    op.rename_table("ram", "ram_kits")
    op.execute("UPDATE pc_parts SET part_type = 'ramkit' WHERE part_type = 'ram'")
    op.add_column("ram_kits", sa.Column("ram_group_id", sa.UUID(), nullable=True))
    op.execute(
        """
        UPDATE ram_kits r SET ram_group_id = g.id FROM ram_groups g
        WHERE g.ddr_generation = r.ddr_generation AND g.speed_mhz = r.speed_mhz
          AND g.capacity_gb = r.capacity_gb AND g.modules = r.modules
        """
    )
    op.alter_column("ram_kits", "ram_group_id", nullable=False)
    op.create_index(op.f("ix_ram_kits_ram_group_id"), "ram_kits", ["ram_group_id"], unique=False)
    op.create_foreign_key(
        "fk_ram_kits_ram_group_id", "ram_kits", "ram_groups", ["ram_group_id"], ["id"], ondelete="RESTRICT"
    )
    for col in (
        "ddr_generation", "speed_mhz", "modules", "capacity_gb", "module_capacity_gb",
        "cas_latency", "voltage", "is_ecc",
    ):
        op.drop_column("ram_kits", col)

    # --- Storage: one group per (type, interface, capacity); then rename table ---
    op.execute(
        """
        INSERT INTO storage_groups
            (id, name, storage_type, form_factor, interface, capacity_gb, read_speed_mbps,
             write_speed_mbps, has_dram_cache, endurance_tbw, rpm)
        SELECT DISTINCT ON (storage_type, interface, capacity_gb)
               gen_random_uuid(),
               capacity_gb || 'GB ' || interface || ' ' || storage_type,
               storage_type, form_factor, interface, capacity_gb, read_speed_mbps,
               write_speed_mbps, has_dram_cache, endurance_tbw, rpm
        FROM storage ORDER BY storage_type, interface, capacity_gb, id
        """
    )
    op.rename_table("storage", "storage_drives")
    op.execute("UPDATE pc_parts SET part_type = 'storagedrive' WHERE part_type = 'storage'")
    op.add_column("storage_drives", sa.Column("storage_group_id", sa.UUID(), nullable=True))
    op.execute(
        """
        UPDATE storage_drives s SET storage_group_id = g.id FROM storage_groups g
        WHERE g.storage_type = s.storage_type AND g.interface = s.interface
          AND g.capacity_gb = s.capacity_gb
        """
    )
    op.alter_column("storage_drives", "storage_group_id", nullable=False)
    op.create_index(op.f("ix_storage_drives_storage_group_id"), "storage_drives", ["storage_group_id"], unique=False)
    op.create_foreign_key(
        "fk_storage_drives_storage_group_id", "storage_drives", "storage_groups",
        ["storage_group_id"], ["id"], ondelete="RESTRICT",
    )
    for col in (
        "storage_type", "form_factor", "interface", "capacity_gb", "read_speed_mbps",
        "write_speed_mbps", "has_dram_cache", "endurance_tbw", "rpm",
    ):
        op.drop_column("storage_drives", col)


def downgrade():
    # --- GPU: re-add flat columns, backfill from chipset, drop group ---
    op.add_column("gpus", sa.Column("chipset", sa.String(length=50), nullable=True))
    op.add_column("gpus", sa.Column("vram_gb", sa.Integer(), nullable=True))
    op.add_column("gpus", sa.Column("tdp_watts", sa.Integer(), nullable=True))
    op.add_column("gpus", sa.Column("recommended_psu_watts", sa.Integer(), nullable=True))
    op.add_column("gpus", sa.Column("supported_features", postgresql.ARRAY(sa.String()), nullable=True))
    op.add_column("gpus", sa.Column("benchmark_scores", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("gpus", sa.Column("vram_type", sa.String(length=20), nullable=True))
    op.add_column("gpus", sa.Column("pcie_generation", sa.Integer(), nullable=True))
    op.add_column("gpus", sa.Column("base_clock_mhz", sa.Integer(), nullable=True))
    op.add_column("gpus", sa.Column("boost_clock_mhz", sa.Integer(), nullable=True))
    op.add_column("gpus", sa.Column("has_ray_tracing", sa.Boolean(), nullable=True))
    op.add_column("gpus", sa.Column("cuda_cores", sa.Integer(), nullable=True))
    op.add_column("gpus", sa.Column("tensor_cores", sa.Integer(), nullable=True))
    op.add_column("gpus", sa.Column("stream_processors", sa.Integer(), nullable=True))
    op.add_column("gpus", sa.Column("matrix_cores", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE gpus g SET
            chipset = c.name, vram_gb = c.vram_gb, tdp_watts = c.tdp_watts,
            recommended_psu_watts = c.recommended_psu_watts, supported_features = c.supported_features,
            benchmark_scores = c.benchmark_scores, vram_type = c.vram_type,
            pcie_generation = c.pcie_generation, base_clock_mhz = c.base_clock_mhz,
            boost_clock_mhz = c.boost_clock_mhz, has_ray_tracing = c.has_ray_tracing,
            cuda_cores = c.cuda_cores, tensor_cores = c.tensor_cores,
            stream_processors = c.stream_processors, matrix_cores = c.matrix_cores
        FROM gpu_chipsets c WHERE c.id = g.gpu_chipset_id
        """
    )
    op.alter_column("gpus", "chipset", nullable=False)
    op.alter_column("gpus", "vram_gb", nullable=False)
    op.alter_column("gpus", "tdp_watts", nullable=False)
    op.drop_constraint("fk_gpus_gpu_chipset_id", "gpus", type_="foreignkey")
    op.drop_index(op.f("ix_gpus_gpu_chipset_id"), table_name="gpus")
    op.drop_column("gpus", "gpu_chipset_id")

    # --- PSU ---
    op.add_column("psus", sa.Column("wattage", sa.Integer(), nullable=True))
    op.add_column("psus", sa.Column("form_factor", sa.String(length=10), nullable=True))
    op.add_column("psus", sa.Column("efficiency_rating", sa.String(length=30), nullable=True))
    op.add_column("psus", sa.Column("pcie_8pin_connectors", sa.Integer(), nullable=True))
    op.add_column("psus", sa.Column("pcie_12pin_connectors", sa.Integer(), nullable=True))
    op.add_column("psus", sa.Column("pcie_16pin_connectors", sa.Integer(), nullable=True))
    op.add_column("psus", sa.Column("modular", sa.String(length=10), nullable=True))
    op.add_column("psus", sa.Column("eps_connectors", sa.Integer(), nullable=True))
    op.add_column("psus", sa.Column("fan_size_mm", sa.Integer(), nullable=True))
    op.add_column("psus", sa.Column("is_fanless", sa.Boolean(), nullable=True))
    op.execute(
        """
        UPDATE psus p SET
            wattage = g.wattage, form_factor = g.form_factor, efficiency_rating = g.efficiency_rating,
            pcie_8pin_connectors = g.pcie_8pin_connectors, pcie_12pin_connectors = g.pcie_12pin_connectors,
            pcie_16pin_connectors = g.pcie_16pin_connectors, modular = g.modular,
            eps_connectors = g.eps_connectors, fan_size_mm = g.fan_size_mm, is_fanless = g.is_fanless
        FROM psu_groups g WHERE g.id = p.psu_group_id
        """
    )
    op.alter_column("psus", "wattage", nullable=False)
    op.alter_column("psus", "form_factor", nullable=False)
    op.alter_column("psus", "efficiency_rating", nullable=False)
    op.drop_constraint("fk_psus_psu_group_id", "psus", type_="foreignkey")
    op.drop_index(op.f("ix_psus_psu_group_id"), table_name="psus")
    op.drop_column("psus", "psu_group_id")

    # --- RAM (rename back first) ---
    op.rename_table("ram_kits", "ram")
    op.execute("UPDATE pc_parts SET part_type = 'ram' WHERE part_type = 'ramkit'")
    op.add_column("ram", sa.Column("ddr_generation", sa.String(length=10), nullable=True))
    op.add_column("ram", sa.Column("speed_mhz", sa.Integer(), nullable=True))
    op.add_column("ram", sa.Column("modules", sa.Integer(), nullable=True))
    op.add_column("ram", sa.Column("capacity_gb", sa.Integer(), nullable=True))
    op.add_column("ram", sa.Column("module_capacity_gb", sa.Integer(), nullable=True))
    op.add_column("ram", sa.Column("cas_latency", sa.Integer(), nullable=True))
    op.add_column("ram", sa.Column("voltage", sa.Float(), nullable=True))
    op.add_column("ram", sa.Column("is_ecc", sa.Boolean(), nullable=True))
    op.execute(
        """
        UPDATE ram r SET
            ddr_generation = g.ddr_generation, speed_mhz = g.speed_mhz, modules = g.modules,
            capacity_gb = g.capacity_gb, module_capacity_gb = g.module_capacity_gb,
            cas_latency = g.cas_latency, voltage = g.voltage, is_ecc = g.is_ecc
        FROM ram_groups g WHERE g.id = r.ram_group_id
        """
    )
    op.alter_column("ram", "ddr_generation", nullable=False)
    op.alter_column("ram", "speed_mhz", nullable=False)
    op.alter_column("ram", "modules", nullable=False)
    op.alter_column("ram", "capacity_gb", nullable=False)
    op.drop_constraint("fk_ram_kits_ram_group_id", "ram", type_="foreignkey")
    op.drop_index(op.f("ix_ram_kits_ram_group_id"), table_name="ram")
    op.drop_column("ram", "ram_group_id")

    # --- Storage (rename back first) ---
    op.rename_table("storage_drives", "storage")
    op.execute("UPDATE pc_parts SET part_type = 'storage' WHERE part_type = 'storagedrive'")
    op.add_column("storage", sa.Column("storage_type", sa.String(length=20), nullable=True))
    op.add_column("storage", sa.Column("form_factor", sa.String(length=20), nullable=True))
    op.add_column("storage", sa.Column("interface", sa.String(length=20), nullable=True))
    op.add_column("storage", sa.Column("capacity_gb", sa.Integer(), nullable=True))
    op.add_column("storage", sa.Column("read_speed_mbps", sa.Integer(), nullable=True))
    op.add_column("storage", sa.Column("write_speed_mbps", sa.Integer(), nullable=True))
    op.add_column("storage", sa.Column("has_dram_cache", sa.Boolean(), nullable=True))
    op.add_column("storage", sa.Column("endurance_tbw", sa.Integer(), nullable=True))
    op.add_column("storage", sa.Column("rpm", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE storage s SET
            storage_type = g.storage_type, form_factor = g.form_factor, interface = g.interface,
            capacity_gb = g.capacity_gb, read_speed_mbps = g.read_speed_mbps,
            write_speed_mbps = g.write_speed_mbps, has_dram_cache = g.has_dram_cache,
            endurance_tbw = g.endurance_tbw, rpm = g.rpm
        FROM storage_groups g WHERE g.id = s.storage_group_id
        """
    )
    op.alter_column("storage", "storage_type", nullable=False)
    op.alter_column("storage", "form_factor", nullable=False)
    op.alter_column("storage", "interface", nullable=False)
    op.alter_column("storage", "capacity_gb", nullable=False)
    op.drop_constraint("fk_storage_drives_storage_group_id", "storage", type_="foreignkey")
    op.drop_index(op.f("ix_storage_drives_storage_group_id"), table_name="storage")
    op.drop_column("storage", "storage_group_id")

    # --- drop group tables ---
    for tbl in ("storage_groups", "ram_groups", "psu_groups", "gpu_chipsets"):
        op.drop_index(op.f(f"ix_{tbl}_id"), table_name=tbl)
        op.drop_table(tbl)
