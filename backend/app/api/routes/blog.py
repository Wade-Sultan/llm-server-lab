from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_async_db
from app.models.blog import BlogPost, BlogPostStatus
from app.schemas.blog import BlogPostDetail, BlogPostList, BlogPostSummary

router = APIRouter(prefix="/blog", tags=["blog"])

# Columns for the index listing. Selecting explicitly keeps the (potentially
# large) Markdown body off the wire for list requests.
_SUMMARY_COLUMNS = (
    BlogPost.id,
    BlogPost.slug,
    BlogPost.title,
    BlogPost.excerpt,
    BlogPost.cover_image_url,
    BlogPost.cover_image_alt,
    BlogPost.author_name,
    BlogPost.tags,
    BlogPost.published_at,
    BlogPost.reading_minutes,
    BlogPost.is_featured,
)


def _summary(row) -> BlogPostSummary:
    return BlogPostSummary(
        id=row.id,
        slug=row.slug,
        title=row.title,
        excerpt=row.excerpt,
        cover_image_url=row.cover_image_url,
        cover_image_alt=row.cover_image_alt,
        author_name=row.author_name,
        tags=row.tags or [],
        published_at=row.published_at,
        reading_minutes=row.reading_minutes,
        is_featured=row.is_featured,
    )


@router.get("/posts", response_model=BlogPostList)
async def list_posts(
    db: AsyncSession = Depends(get_async_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    tag: str | None = None,
) -> BlogPostList:
    """Published posts, featured first then newest first.

    Public and unauthenticated — drafts are never exposed here.
    """
    where = [BlogPost.status == BlogPostStatus.PUBLISHED.value]
    if tag:
        where.append(BlogPost.tags.any(tag))

    count_result = await db.execute(
        select(func.count(BlogPost.id)).where(*where)
    )
    count = count_result.scalar_one()

    rows_result = await db.execute(
        select(*_SUMMARY_COLUMNS)
        .where(*where)
        .order_by(
            BlogPost.is_featured.desc(),
            BlogPost.published_at.desc().nullslast(),
        )
        .offset(skip)
        .limit(limit)
    )

    return BlogPostList(
        data=[_summary(row) for row in rows_result.all()], count=count
    )


@router.get("/posts/{slug}", response_model=BlogPostDetail)
async def get_post(
    slug: str,
    db: AsyncSession = Depends(get_async_db),
) -> BlogPostDetail:
    """A single published post by slug, including its Markdown body."""
    result = await db.execute(
        select(BlogPost).where(
            BlogPost.slug == slug,
            BlogPost.status == BlogPostStatus.PUBLISHED.value,
        )
    )
    post = result.scalar_one_or_none()

    if post is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Post not found"
        )

    return BlogPostDetail.model_validate(post)
