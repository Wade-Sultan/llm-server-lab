import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, with_polymorphic

from app.data.refbuilds import BUILDS, Build, Part
from app.models.listing import AmazonListing
from app.models.pcparts import PCPart
from app.models.reference_build import ReferenceBuild, ReferenceBuildPart

# Reference-build parts loaded with every subclass column, so the group FKs
# (gpu_chipset_id, psu_group_id, ...) are present for resolve_part_price_cents.
# A plain joinedload of the polymorphic base leaves them unloaded, and reading
# one would be an async-unsafe lazy load.
_PART_POLY = with_polymorphic(PCPart, "*")

logger = logging.getLogger(__name__)


async def get_all_active(db: AsyncSession) -> dict[str, Build]:
    try:
        stmt = (
            select(ReferenceBuild)
            .options(
                joinedload(ReferenceBuild.parts).joinedload(ReferenceBuildPart.part)
            )
            .where(ReferenceBuild.is_active == True)  # noqa: E712
        )
        result = await db.execute(stmt)
        rows = result.unique().scalars().all()
        part_ids = [rbp.part_id for row in rows for rbp in row.parts]
        amazon_urls = await get_amazon_urls_by_part(db, part_ids)
        return {row.build_key: _to_build(row, amazon_urls) for row in rows}
    except Exception as e:
        logger.warning(
            "DB query for reference builds failed, falling back to static data: %s", e
        )
        return dict(BUILDS)


async def get_by_key(db: AsyncSession, build_key: str) -> tuple[str, Build] | None:
    try:
        stmt = (
            select(ReferenceBuild)
            .options(
                joinedload(ReferenceBuild.parts).joinedload(ReferenceBuildPart.part)
            )
            .where(
                ReferenceBuild.build_key == build_key,
                ReferenceBuild.is_active.is_(True),
            )
        )
        result = await db.execute(stmt)
        row = result.unique().scalars().first()
        if row is None:
            return BUILDS.get(build_key) and (build_key, BUILDS[build_key])
        amazon_urls = await get_amazon_urls_by_part(
            db, [rbp.part_id for rbp in row.parts]
        )
        return build_key, _to_build(row, amazon_urls)
    except Exception as e:
        logger.warning(
            "DB query for build key '%s' failed, falling back to static data: %s",
            build_key,
            e,
        )
        build = BUILDS.get(build_key)
        return (build_key, build) if build else None


# A reference build has to have this fraction of its parts priced on both sides
# before its ratio is trusted. A build with two of nine parts priced produces a
# ratio describing those two parts, not the market.
_MIN_PRICE_COVERAGE = 0.8


async def market_drift_factor(db: AsyncSession) -> float | None:
    """How much the reference builds cost NOW versus their curated snapshots.

    WHY A SINGLE RATIO RATHER THAN A PRICE PER TIER. The obvious move is to
    anchor each budget tier to the live total of the reference build that tier
    resolves to. It does not work: resolve_build maps several tiers onto one
    build (entry and mid 1440p gaming are both '1440_mid'), so anchoring
    directly would silently collapse tiers that today differ by $500 and would
    hand a server profile whatever the two server builds happen to cost. The
    ladder's *shape* — the spacing between tiers, and the server ladder sitting
    above the desktop one — is hand-tuned and worth keeping.

    So this measures only what the constants cannot know: which direction the
    market has moved since someone last reviewed them. Callers scale the whole
    ladder by it, preserving every relative decision while the absolute level
    tracks the catalog.

    The median across builds, not the mean: one reference build holding a part
    that has gone end-of-life and spiked should not drag every tier up with it.

    Returns None when nothing can be measured (no active builds, no live prices
    backfilled, a query failure) — callers then use the constants unscaled,
    which is exactly today's behaviour.
    """
    from app.crud.components import resolve_part_price_cents

    try:
        stmt = (
            select(ReferenceBuild)
            .options(
                joinedload(ReferenceBuild.parts).joinedload(
                    ReferenceBuildPart.part.of_type(_PART_POLY)
                )
            )
            .where(ReferenceBuild.is_active == True)  # noqa: E712
        )
        result = await db.execute(stmt)
        builds = result.unique().scalars().all()
    except Exception:
        logger.warning(
            "market drift query failed; ladder stays unscaled", exc_info=True
        )
        return None

    ratios: list[float] = []
    for build in builds:
        if not build.parts:
            continue
        live_total = 0
        curated_total = 0
        priced = 0
        for rbp in build.parts:
            if rbp.part is None or not rbp.approx_price:
                continue
            # The grouped types (GPU/PSU/RAM/Storage) carry price on their group
            # rather than on the pc_parts row, which is why this goes through
            # the resolver instead of reading street_price_cents directly.
            live = await resolve_part_price_cents(db, rbp.part)
            if not live:
                continue
            live_total += live
            curated_total += rbp.approx_price
            priced += 1
        if curated_total <= 0 or priced / len(build.parts) < _MIN_PRICE_COVERAGE:
            continue
        ratios.append(live_total / curated_total)

    if not ratios:
        logger.info(
            "no reference build had enough live prices to measure market drift; "
            "budget ladder stays unscaled"
        )
        return None

    ratios.sort()
    mid = len(ratios) // 2
    median = ratios[mid] if len(ratios) % 2 else (ratios[mid - 1] + ratios[mid]) / 2
    logger.info(
        "market drift factor %.3f from %d reference build(s)", median, len(ratios)
    )
    return median


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
