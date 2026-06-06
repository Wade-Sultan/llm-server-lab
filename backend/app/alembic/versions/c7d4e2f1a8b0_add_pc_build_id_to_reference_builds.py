"""add_pc_build_id_to_reference_builds

Revision ID: c7d4e2f1a8b0
Revises: a8c2e4f61b93
Create Date: 2026-06-06

"""
from alembic import op
import sqlalchemy as sa

revision = 'c7d4e2f1a8b0'
down_revision = 'a8c2e4f61b93'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'reference_builds',
        sa.Column('pc_build_id', sa.UUID(), nullable=True),
    )
    op.create_index('ix_reference_builds_pc_build_id', 'reference_builds', ['pc_build_id'])
    op.create_foreign_key(
        'fk_reference_builds_pc_build_id',
        'reference_builds', 'pc_builds',
        ['pc_build_id'], ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('fk_reference_builds_pc_build_id', 'reference_builds', type_='foreignkey')
    op.drop_index('ix_reference_builds_pc_build_id', table_name='reference_builds')
    op.drop_column('reference_builds', 'pc_build_id')
