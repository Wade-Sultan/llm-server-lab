"""add the parts-discovery staging tables

Creates discovery_runs (one row per pipeline execution with status/cost
telemetry), discovery_sources (seed pages for the scheduled job's content-hash
diffing; unused by the on-demand path), and discovered_items (the approval
queue: JSONB extraction snapshot + per-field provenance + dedup result).
Approval in the admin panel is the only path from discovered_items into
pc_parts — the pipeline never writes the catalog directly.

Revision ID: c9d0e1f2a3b4
Revises: d1e2f3a4b5c6
Create Date: 2026-07-18 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "c9d0e1f2a3b4"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "discovery_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, index=True),
        sa.Column("run_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="running"),
        sa.Column("pipeline_version", sa.Text(), nullable=False),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("sources_checked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_new", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("total_cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "discovery_sources",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, index=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_content_hash", sa.Text(), nullable=True),
    )

    op.create_table(
        "discovered_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, index=True),
        sa.Column("run_id", UUID(as_uuid=True),
                  sa.ForeignKey("discovery_runs.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("name_normalized", sa.Text(), nullable=False),
        sa.Column("model_number", sa.Text(), nullable=True),
        sa.Column("extracted_fields", JSONB(), nullable=False),
        sa.Column("field_provenance", JSONB(), nullable=False),
        sa.Column("extraction_confidence", JSONB(), nullable=True),
        sa.Column("source_urls", ARRAY(sa.Text()), nullable=False),
        sa.Column("matched_part_id", UUID(as_uuid=True),
                  sa.ForeignKey("pc_parts.id", ondelete="SET NULL"),
                  nullable=True, index=True),
        sa.Column("matched_chipset_id", UUID(as_uuid=True),
                  sa.ForeignKey("gpu_chipsets.id", ondelete="SET NULL"),
                  nullable=True, index=True),
        sa.Column("match_method", sa.Text(), nullable=True),
        sa.Column("match_score", sa.Float(), nullable=True),
        sa.Column("validation_status", sa.Text(), nullable=False),
        sa.Column("validation_errors", JSONB(), nullable=True),
        sa.Column("review_status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_part_id", UUID(as_uuid=True),
                  sa.ForeignKey("pc_parts.id", ondelete="SET NULL"),
                  nullable=True, index=True),
        sa.Column("created_chipset_id", UUID(as_uuid=True),
                  sa.ForeignKey("gpu_chipsets.id", ondelete="SET NULL"),
                  nullable=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    op.create_index(
        "uq_discovered_items_pending_cat_name",
        "discovered_items",
        ["category", "name_normalized"],
        unique=True,
        postgresql_where=sa.text("review_status = 'pending'"),
    )


def downgrade():
    op.drop_index("uq_discovered_items_pending_cat_name", table_name="discovered_items")
    op.drop_table("discovered_items")
    op.drop_table("discovery_sources")
    op.drop_table("discovery_runs")
