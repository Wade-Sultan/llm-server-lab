import uuid

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db.base import Base


class GuideVideo(Base):
    """A PC-building guide video listed on the public /guides page.

    These are links to third-party videos, not hosted media — the catalog
    records where a video lives, and the page embeds or links to it. Replaces
    the hardcoded array that used to live in the frontend component.
    """

    __tablename__ = "guide_videos"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
    )

    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    # The link as entered in the admin, kept verbatim so the original is never
    # lost to a parsing change.
    url = Column(Text, nullable=False)

    # Extracted from `url` on save when it is a recognisable YouTube link. When
    # set, the page renders a lite-youtube embed; when null it falls back to a
    # plain outbound link card, so a non-YouTube URL still works.
    youtube_video_id = Column(String(32), nullable=True)

    is_published = Column(Boolean, nullable=False, server_default="true")

    # Manual ordering: the grid is small and curated, so a hand-set order beats
    # any automatic one. Ties break on created_at.
    sort_order = Column(Integer, nullable=False, server_default="0")

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
