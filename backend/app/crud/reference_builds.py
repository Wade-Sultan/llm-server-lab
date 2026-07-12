import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.config import settings
from app.models.listing import AmazonListing
from app.models.reference_build import ReferenceBuild, ReferenceBuildPart
from app.data.refbuilds import Build, Part, BUILDS

logger = logging.getLogger(__name__)


async def get_all_active(db: AsyncSession) -> dict[str, Build]:
    try:
        stmt = (
            select(ReferenceBuild)
            .options(joinedload(ReferenceBuild.parts).joinedload(ReferenceBuildPart.part))
            .where(ReferenceBuild.is_active == True)  # noqa: E712
        )
        result = await db.execute(stmt)
        rows = result.unique().scalars().all()
        part_ids = [rbp.part_id for row in rows for rbp in row.parts]
        amazon_urls = await get_amazon_urls_by_part(db, part_ids)
        return {row.build_key: _to_build(row, amazon_urls) for row in rows}
    except Exception as e:
        logger.warning("DB query for reference builds failed, falling back to static data: %s", e)
        return dict(BUILDS)


async def get_by_key(db: AsyncSession, build_key: str) -> tuple[str, Build] | None:
    try:
        stmt = (
            select(ReferenceBuild)
            .options(joinedload(ReferenceBuild.parts).joinedload(ReferenceBuildPart.part))
            .where(ReferenceBuild.build_key == build_key, ReferenceBuild.is_active == True)  # noqa: E712
        )
        result = await db.execute(stmt)
        row = result.unique().scalars().first()
        if row is None:
            return BUILDS.get(build_key) and (build_key, BUILDS[build_key])
        amazon_urls = await get_amazon_urls_by_part(db, [rbp.part_id for rbp in row.parts])
        return build_key, _to_build(row, amazon_urls)
    except Exception as e:
        logger.warning("DB query for build key '%s' failed, falling back to static data: %s", build_key, e)
        build = BUILDS.get(build_key)
        return (build_key, build) if build else None


async def get_amazon_urls_by_part(
    db: AsyncSession, part_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Map part_id -> Amazon product page URL, one listing per part."""
    if not part_ids:
        return {}
    stmt = (
        select(AmazonListing)
        .where(AmazonListing.part_id.in_(part_ids), AmazonListing.is_active == True)  # noqa: E712
        .order_by(AmazonListing.created_at)
    )
    result = await db.execute(stmt)
    urls: dict[uuid.UUID, str] = {}
    for listing in result.scalars().all():
        urls.setdefault(listing.part_id, _amazon_product_url(listing))
    return urls


def _amazon_product_url(listing: AmazonListing) -> str:
    if listing.url:
        return listing.url
    url = f"https://www.amazon.com/dp/{listing.asin}"
    if settings.AMAZON_AFFILIATE_TAG:
        url += f"?tag={settings.AMAZON_AFFILIATE_TAG}"
    return url


def _to_build(row: ReferenceBuild, amazon_urls: dict[uuid.UUID, str]) -> Build:
    return Build(
        label=row.label,
        description=row.description,
        total_approx=row.total_approx,
        max_resolution=row.max_resolution,
        parts=[
            Part(
                component=rbp.component,
                brand=rbp.part.manufacturer or "",
                model=rbp.part.name,
                approx_price=rbp.approx_price,
                part_id=str(rbp.part.id),
                amazon_url=amazon_urls.get(rbp.part.id),
            )
            for rbp in sorted(row.parts, key=lambda p: p.sort_order)
        ],
    )
