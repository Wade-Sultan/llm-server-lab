"""add_max_resolution_to_reference_builds

Revision ID: b954e7813984
Revises: c0d08888cb8a
Create Date: 2026-07-04

"""
from alembic import op
import sqlalchemy as sa

revision = 'b954e7813984'
down_revision = 'c0d08888cb8a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('reference_builds', sa.Column('max_resolution', sa.Integer(), nullable=True))
    # Backfill from the existing build_key convention (e.g. "1080_entry") so
    # resolver behavior doesn't regress before these are reviewed in admin.
    op.execute(
        r"""
        UPDATE reference_builds
        SET max_resolution = substring(build_key from '^(\d{4})')::integer
        WHERE build_key ~ '^\d{4}'
        """
    )


def downgrade() -> None:
    op.drop_column('reference_builds', 'max_resolution')
