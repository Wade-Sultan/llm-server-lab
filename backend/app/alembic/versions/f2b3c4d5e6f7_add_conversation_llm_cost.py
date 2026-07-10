"""add_conversation_llm_cost

Adds running LLM cost/token totals to conversations so every OpenRouter call in
a conversation (profile extraction, elicitation, recommendation) rolls up into a
single per-conversation "cost per build". Also adds reached_recommendation to
distinguish conversations that produced a build from elicitation-only chats.

Revision ID: f2b3c4d5e6f7
Revises: e1a2b3c4d5e6
Create Date: 2026-07-10

"""
from alembic import op
import sqlalchemy as sa

revision = 'f2b3c4d5e6f7'
down_revision = 'e1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('conversations', sa.Column('total_cost_usd', sa.Numeric(), server_default='0', nullable=False))
    op.add_column('conversations', sa.Column('total_tokens_in', sa.Integer(), server_default='0', nullable=False))
    op.add_column('conversations', sa.Column('total_tokens_out', sa.Integer(), server_default='0', nullable=False))
    op.add_column('conversations', sa.Column('llm_call_count', sa.Integer(), server_default='0', nullable=False))
    op.add_column('conversations', sa.Column('reached_recommendation', sa.Boolean(), server_default='false', nullable=False))


def downgrade() -> None:
    op.drop_column('conversations', 'reached_recommendation')
    op.drop_column('conversations', 'llm_call_count')
    op.drop_column('conversations', 'total_tokens_out')
    op.drop_column('conversations', 'total_tokens_in')
    op.drop_column('conversations', 'total_cost_usd')
