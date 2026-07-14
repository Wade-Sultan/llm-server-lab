"""move street price to the component group level

Adds street_price_cents to gpu_chipsets / psu_groups / ram_groups /
storage_groups and backfills each from the cheapest existing member's price.
The per-exact pc_parts.street_price_cents column stays (it's shared with the
non-grouped part types) but is no longer the source of truth for grouped parts.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-13 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None

# (group table, exact table, group FK column on the exact)
_GROUPS = [
    ("gpu_chipsets", "gpus", "gpu_chipset_id"),
    ("psu_groups", "psus", "psu_group_id"),
    ("ram_groups", "ram_kits", "ram_group_id"),
    ("storage_groups", "storage_drives", "storage_group_id"),
]


def upgrade():
    for group_tbl, exact_tbl, fk in _GROUPS:
        op.add_column(group_tbl, sa.Column("street_price_cents", sa.Integer(), nullable=True))
        # Backfill from the cheapest member's price.
        op.execute(
            f"""
            UPDATE {group_tbl} g SET street_price_cents = sub.min_price
            FROM (
                SELECT e.{fk} AS gid, MIN(p.street_price_cents) AS min_price
                FROM {exact_tbl} e JOIN pc_parts p ON p.id = e.id
                GROUP BY e.{fk}
            ) sub
            WHERE sub.gid = g.id
            """
        )


def downgrade():
    for group_tbl, _exact_tbl, _fk in _GROUPS:
        op.drop_column(group_tbl, "street_price_cents")
