"""add_conversation_models_used

Adds a models_used array to conversations, tracking the distinct OpenRouter
model names invoked across a conversation's turns (extraction, elicitation,
recommendation can each use a different model).

Revision ID: a3c9d1e5f6b2
Revises: f2b3c4d5e6f7
Create Date: 2026-07-10

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'a3c9d1e5f6b2'
down_revision = 'f2b3c4d5e6f7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'conversations',
        sa.Column('models_used', postgresql.ARRAY(sa.String()), server_default='{}', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('conversations', 'models_used')
