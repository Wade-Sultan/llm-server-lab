"""add paused_builds — pipelines stopped at the case step

Durable backstop for the case-pick pause. Valkey holds the fast copy; this is
what a resume falls back to after an eviction or a TTL, because losing the
payload costs nine LLM calls rather than a cache miss.

Revision ID: c1d3e5f7a9b2
Revises: b8c2d4e6f0a1
Create Date: 2026-08-16 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "c1d3e5f7a9b2"
down_revision = "b8c2d4e6f0a1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "paused_builds",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("conversation_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("resumed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_paused_builds_token", "paused_builds", ["token"], unique=True)
    op.create_index(
        "ix_paused_builds_conversation_id", "paused_builds", ["conversation_id"]
    )
    # Sweeping old rows (and, later, recording never-resumed builds as
    # ABANDONED telemetry) scans by age, so the ordering column is indexed.
    op.create_index("ix_paused_builds_created_at", "paused_builds", ["created_at"])


def downgrade():
    op.drop_index("ix_paused_builds_created_at", table_name="paused_builds")
    op.drop_index("ix_paused_builds_conversation_id", table_name="paused_builds")
    op.drop_index("ix_paused_builds_token", table_name="paused_builds")
    op.drop_table("paused_builds")
