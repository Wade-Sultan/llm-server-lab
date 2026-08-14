"""add listing lookup failures

One row per part the listings API could not produce a listing for — either a
coverage gap (the part is recommended but has nothing active to buy) or a
genuine lookup error. Written by commerce; read by the admin page and by the
digest email commerce sends.

Keyed by part_id rather than being an append-only log: the build card fetches
listings per part on every render, so one broken part is hit dozens of times a
day and an event log would be almost entirely duplicates.

Revision ID: c3e5a7b9d1f4
Revises: b8d2f4a6c1e3
Create Date: 2026-08-13 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "c3e5a7b9d1f4"
down_revision = "b8d2f4a6c1e3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "listing_lookup_failures",
        sa.Column(
            "part_id",
            UUID(as_uuid=True),
            sa.ForeignKey("pc_parts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("occurrences", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Both readers — the admin page and the digest — want open rows only, so
    # the index is partial rather than covering resolved history nobody scans.
    op.create_index(
        "ix_listing_lookup_failures_open",
        "listing_lookup_failures",
        ["last_seen_at"],
        postgresql_where=sa.text("resolved_at IS NULL"),
    )


def downgrade():
    op.drop_index(
        "ix_listing_lookup_failures_open", table_name="listing_lookup_failures"
    )
    op.drop_table("listing_lookup_failures")
