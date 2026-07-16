"""add_reference_build_to_conversations

Revision ID: e2f3a4b5c6d7
Revises: b7c8d9e0f1a2
Create Date: 2026-07-16

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'e2f3a4b5c6d7'
down_revision = 'b7c8d9e0f1a2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('conversations', sa.Column('reference_build_key', sa.Text(), nullable=True))
    op.add_column('conversations', sa.Column('reference_build', postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column('conversations', 'reference_build')
    op.drop_column('conversations', 'reference_build_key')
