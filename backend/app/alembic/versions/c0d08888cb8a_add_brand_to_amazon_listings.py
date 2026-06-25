"""add_brand_to_amazon_listings

Revision ID: c0d08888cb8a
Revises: 1d9f3b7c2a44
Create Date: 2026-06-24

"""
from alembic import op
import sqlalchemy as sa

revision = 'c0d08888cb8a'
down_revision = '1d9f3b7c2a44'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('amazon_listings', sa.Column('brand', sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column('amazon_listings', 'brand')
