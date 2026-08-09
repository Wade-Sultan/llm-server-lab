from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict


class GuideVideoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str | None = None
    url: str
    youtube_video_id: str | None = None


class GuideVideoList(BaseModel):
    data: list[GuideVideoOut]
    count: int
