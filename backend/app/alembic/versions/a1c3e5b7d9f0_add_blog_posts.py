"""add the blog_posts table

Backs the editorial blog: authored in the admin panel (Prisma) and read by the
public /blog pages on the frontend through the FastAPI read endpoints. Body is
Markdown; images live in GCS and are referenced by URL, so nothing binary is
stored here.

Revision ID: a1c3e5b7d9f0
Revises: c4f7a1b8d2e3
Create Date: 2026-08-09 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID

revision = "a1c3e5b7d9f0"
down_revision = "c4f7a1b8d2e3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "blog_posts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, index=True),
        sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column(
            "content_markdown", sa.Text(), nullable=False, server_default=""
        ),
        sa.Column("cover_image_url", sa.Text(), nullable=True),
        sa.Column("cover_image_alt", sa.String(255), nullable=True),
        sa.Column("author_name", sa.String(120), nullable=True),
        sa.Column(
            "tags", ARRAY(sa.String()), nullable=False, server_default="{}"
        ),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="draft"
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reading_minutes", sa.Integer(), nullable=True),
        sa.Column(
            "is_featured", sa.Boolean(), nullable=False, server_default="false"
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
    op.create_index("ix_blog_posts_slug", "blog_posts", ["slug"], unique=True)
    # The public list query is always "published, newest first".
    op.create_index(
        "ix_blog_posts_status_published_at",
        "blog_posts",
        ["status", "published_at"],
    )


def downgrade():
    op.drop_index("ix_blog_posts_status_published_at", table_name="blog_posts")
    op.drop_index("ix_blog_posts_slug", table_name="blog_posts")
    op.drop_table("blog_posts")
