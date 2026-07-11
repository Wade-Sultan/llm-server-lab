"""add_reference_build_to_build_sessions

Records the reference build resolved in parallel with each DSPy run. When the
DSPy build succeeds the customer sees it, but the reference build is still
captured here so every successful run carries a DSPy-vs-reference pair.

Revision ID: f7b3c2a9d4e1
Revises: a3c9d1e5f6b2
Create Date: 2026-07-10

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'f7b3c2a9d4e1'
down_revision = 'a3c9d1e5f6b2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'build_sessions',
        sa.Column('reference_build_key', sa.Text(), nullable=True),
    )
    op.add_column(
        'build_sessions',
        sa.Column(
            'reference_build',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column('build_sessions', 'reference_build')
    op.drop_column('build_sessions', 'reference_build_key')
