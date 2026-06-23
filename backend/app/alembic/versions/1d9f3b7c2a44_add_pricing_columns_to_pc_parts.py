"""add_pricing_columns_to_pc_parts

Revision ID: 1d9f3b7c2a44
Revises: c7d4e2f1a8b0
Create Date: 2026-06-23

"""
from alembic import op
import sqlalchemy as sa

revision = '1d9f3b7c2a44'
down_revision = 'c7d4e2f1a8b0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('pc_parts', sa.Column('msrp_cents', sa.Integer(), nullable=True))
    op.add_column('pc_parts', sa.Column('street_price_cents', sa.Integer(), nullable=True))
    op.add_column('pc_parts', sa.Column('price_source', sa.String(length=20), nullable=True))
    op.add_column('pc_parts', sa.Column('used_market_viable', sa.Boolean(), server_default='false', nullable=True))


def downgrade() -> None:
    op.drop_column('pc_parts', 'used_market_viable')
    op.drop_column('pc_parts', 'price_source')
    op.drop_column('pc_parts', 'street_price_cents')
    op.drop_column('pc_parts', 'msrp_cents')
