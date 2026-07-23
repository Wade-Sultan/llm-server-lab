"""add the pricing ETL tables

Creates pricing_runs (one row per Cloud Run Job execution, mirrors
discovery_runs), price_checks (one row per part/group checked — raw SerpAPI
results plus computed stats, mirrors discovered_items), and serpapi_quota (a
one-row-per-month search counter for the 1000/month budget). Also adds
last_price_checked_at (rotation bookkeeping) to pc_parts and to the four group
tables that carry their own street_price_cents, plus price_source on those
same group tables so they can record provenance the way pc_parts already does.

Revision ID: a1b2c3d4e5f6
Revises: c9d0e1f2a3b4
Create Date: 2026-07-23 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "a1b2c3d4e5f6"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


_GROUP_TABLES = ["gpu_chipsets", "psu_groups", "ram_groups", "storage_groups"]


def upgrade():
    op.create_table(
        "pricing_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, index=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="running"),
        sa.Column("parts_checked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("searches_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "price_checks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, index=True),
        sa.Column("run_id", UUID(as_uuid=True),
                  sa.ForeignKey("pricing_runs.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("target_kind", sa.Text(), nullable=False),
        sa.Column("target_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("queries_used", ARRAY(sa.Text()), nullable=False),
        sa.Column("raw_results", JSONB(), nullable=False),
        sa.Column("n_results_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_results_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("price_mean_cents", sa.Integer(), nullable=True),
        sa.Column("price_min_cents", sa.Integer(), nullable=True),
        sa.Column("price_max_cents", sa.Integer(), nullable=True),
        sa.Column("price_median_cents", sa.Integer(), nullable=True),
        sa.Column("price_stddev_cents", sa.Integer(), nullable=True),
        sa.Column("applied", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    op.create_table(
        "serpapi_quota",
        sa.Column("month", sa.Date(), primary_key=True),
        sa.Column("search_count", sa.Integer(), nullable=False, server_default="0"),
    )

    op.add_column(
        "pc_parts",
        sa.Column("last_price_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    for table in _GROUP_TABLES:
        op.add_column(table, sa.Column("price_source", sa.String(20), nullable=True))
        op.add_column(
            table,
            sa.Column("last_price_checked_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade():
    for table in _GROUP_TABLES:
        op.drop_column(table, "last_price_checked_at")
        op.drop_column(table, "price_source")
    op.drop_column("pc_parts", "last_price_checked_at")

    op.drop_table("serpapi_quota")
    op.drop_table("price_checks")
    op.drop_table("pricing_runs")
