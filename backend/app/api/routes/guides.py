from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_async_db
from app.models.guide_video import GuideVideo
from app.schemas.guide_video import GuideVideoList, GuideVideoOut

router = APIRouter(prefix="/guides", tags=["guides"])


@router.get("/videos", response_model=GuideVideoList)
async def list_guide_videos(
    db: AsyncSession = Depends(get_async_db),
) -> GuideVideoList:
    """Published guide videos in the order set in the admin panel.

    Public and unauthenticated. The catalog is small and curated, so it is
    returned whole — the page filters client-side.
    """
    result = await db.execute(
        select(GuideVideo)
        .where(GuideVideo.is_published.is_(True))
        .order_by(GuideVideo.sort_order.asc(), GuideVideo.created_at.asc())
    )
    videos = result.scalars().all()

    return GuideVideoList(
        data=[GuideVideoOut.model_validate(v) for v in videos],
        count=len(videos),
    )
