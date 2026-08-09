"""add the guide_videos table

Moves the PC-building guide videos out of the hardcoded array in the frontend's
VideoGuides component and into a table managed from the admin panel. These are
links to third-party videos, not hosted media.

The three videos the page shipped with are seeded here so /guides does not go
blank between this migration and the first admin edit.

Revision ID: b2d4f6a8c0e1
Revises: a1c3e5b7d9f0
Create Date: 2026-08-09 00:00:00.000000

"""
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "b2d4f6a8c0e1"
down_revision = "a1c3e5b7d9f0"
branch_labels = None
depends_on = None


# Carried over verbatim from frontend/src/components/pages/VideoGuides.tsx.
_SEED = [
    ("s1fxZ-VWs2U", "PC Building Guide 2024", 0),
    ("gNMQFT2HAiY", "How to install Windows 11", 1),
    ("Ogd1HT9v4Rs", "Intel vs AMD CPU", 2),
]


def upgrade():
    guide_videos = op.create_table(
        "guide_videos",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, index=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("youtube_video_id", sa.String(32), nullable=True),
        sa.Column(
            "is_published", sa.Boolean(), nullable=False, server_default="true"
        ),
        sa.Column(
            "sort_order", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_guide_videos_published_order",
        "guide_videos",
        ["is_published", "sort_order"],
    )

    # ids generated here rather than by a column default, so the column matches
    # every other table in this schema (the models supply uuid4 themselves).
    op.bulk_insert(
        guide_videos,
        [
            {
                "id": uuid.uuid4(),
                "title": title,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "youtube_video_id": video_id,
                "sort_order": order,
            }
            for video_id, title, order in _SEED
        ],
    )


def downgrade():
    op.drop_index("ix_guide_videos_published_order", table_name="guide_videos")
    op.drop_table("guide_videos")
