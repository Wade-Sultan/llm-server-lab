"""add_graph_checkpoint_to_conversations

Write-behind mirror of the LangGraph checkpointer's latest checkpoint per
conversation. Valkey holds the full history for GRAPH_CHECKPOINT_TTL_S; this
column is what outlives it. See app/services/graph/checkpoint.py.

Revision ID: c4f7a1b8d2e3
Revises: b3d5e7f9a1c2
Create Date: 2026-08-07

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c4f7a1b8d2e3"
down_revision = "b3d5e7f9a1c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations", sa.Column("graph_checkpoint", postgresql.JSONB(), nullable=True)
    )
    op.add_column(
        "conversations", sa.Column("graph_checkpoint_id", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("conversations", "graph_checkpoint_id")
    op.drop_column("conversations", "graph_checkpoint")
