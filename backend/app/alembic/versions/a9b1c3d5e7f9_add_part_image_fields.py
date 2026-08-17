"""add image + attribution columns to pc_parts

Product images for the case-picker cards (and eventually other parts). The
attribution columns are not optional metadata: images are sourced from
manufacturer press/product pages, so every image stored here must be able to
say whose it is and where it came from.

Revision ID: a9b1c3d5e7f9
Revises: d4f6b8a0c2e5
Create Date: 2026-08-16 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = "a9b1c3d5e7f9"
down_revision = "d4f6b8a0c2e5"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("pc_parts", sa.Column("image_url", sa.String(500), nullable=True))
    op.add_column("pc_parts", sa.Column("image_credit", sa.String(255), nullable=True))
    op.add_column(
        "pc_parts", sa.Column("image_source_url", sa.String(500), nullable=True)
    )
    op.add_column(
        "pc_parts",
        sa.Column(
            "image_license",
            sa.String(100),
            nullable=True,
            comment="e.g. 'manufacturer press kit', 'CC BY 4.0'",
        ),
    )


def downgrade():
    op.drop_column("pc_parts", "image_license")
    op.drop_column("pc_parts", "image_source_url")
    op.drop_column("pc_parts", "image_credit")
    op.drop_column("pc_parts", "image_url")
