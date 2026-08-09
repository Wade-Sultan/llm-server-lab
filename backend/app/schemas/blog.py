from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class BlogPostSummary(BaseModel):
    """A post as it appears in the index listing — no body, to keep it light."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    title: str
    excerpt: str | None = None
    cover_image_url: str | None = None
    cover_image_alt: str | None = None
    author_name: str | None = None
    tags: list[str] = []
    published_at: datetime | None = None
    reading_minutes: int | None = None
    is_featured: bool = False


class BlogPostDetail(BlogPostSummary):
    """A single post, including its Markdown body."""

    content_markdown: str


class BlogPostList(BaseModel):
    data: list[BlogPostSummary]
    count: int
