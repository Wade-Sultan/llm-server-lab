import enum
import uuid

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.sql import func

from app.db.base import Base


class BlogPostStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"


class BlogPost(Base):
    """A long-form marketing/editorial post, authored in the admin panel.

    Body is stored as Markdown (not HTML): the admin editor is WYSIWYG but
    serializes to Markdown, and the frontend already renders Markdown with
    react-markdown + remark-gfm. Keeping the canonical form as Markdown means
    nothing has to sanitize author HTML on the read path.
    """

    __tablename__ = "blog_posts"

    __table_args__ = (
        # The public list endpoint is always "published, newest first".
        Index(
            "ix_blog_posts_status_published_at",
            "status",
            "published_at",
        ),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
    )

    slug = Column(String(255), nullable=False, unique=True, index=True)
    title = Column(String(255), nullable=False)

    # Short teaser shown on the index page and used as the SEO description.
    excerpt = Column(Text, nullable=True)

    content_markdown = Column(Text, nullable=False, server_default="")

    cover_image_url = Column(Text, nullable=True)
    # Alt text for the cover image; without it the index page is unreadable to
    # screen readers, so it is authored alongside the image.
    cover_image_alt = Column(String(255), nullable=True)

    author_name = Column(String(120), nullable=True)

    tags = Column(ARRAY(String), nullable=False, server_default="{}")

    # draft | published — see BlogPostStatus.
    status = Column(
        String(20),
        nullable=False,
        server_default=BlogPostStatus.DRAFT.value,
    )

    # Set when the post first goes live; drives ordering and the visible date.
    # Nullable because drafts have never been published.
    published_at = Column(DateTime(timezone=True), nullable=True)

    # Estimated minutes to read, computed from the body on save.
    reading_minutes = Column(Integer, nullable=True)

    # Pins a post to the top of the index regardless of date.
    is_featured = Column(Boolean, nullable=False, server_default="false")

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
