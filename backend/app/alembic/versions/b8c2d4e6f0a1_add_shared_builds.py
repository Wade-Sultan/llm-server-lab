"""add shared_builds — public snapshots behind share tokens

One row per generated build, written mid-turn when the build payload is
assembled. Backs the public build page (/b/{token}) and the PDF export.
conversation_id is deliberately not a foreign key: the row is written before
the conversation row necessarily exists, and guest builds never get one.

Revision ID: b8c2d4e6f0a1
Revises: a9b1c3d5e7f9
Create Date: 2026-08-16 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "b8c2d4e6f0a1"
down_revision = "a9b1c3d5e7f9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "shared_builds",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("token", sa.String(32), nullable=False),
        sa.Column("build", JSONB, nullable=False),
        sa.Column("build_key", sa.String(100), nullable=True),
        sa.Column("conversation_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_shared_builds_token", "shared_builds", ["token"], unique=True
    )
    op.create_index(
        "ix_shared_builds_conversation_id", "shared_builds", ["conversation_id"]
    )


def downgrade():
    op.drop_index("ix_shared_builds_conversation_id", table_name="shared_builds")
    op.drop_index("ix_shared_builds_token", table_name="shared_builds")
    op.drop_table("shared_builds")
