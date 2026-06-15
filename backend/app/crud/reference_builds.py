import logging
from sqlalchemy.orm import Session, joinedload
from app.models.reference_build import ReferenceBuild, ReferenceBuildPart
from app.data.refbuilds import Build, Part, BUILDS

logger = logging.getLogger(__name__)


def get_all_active(db: Session) -> dict[str, Build]:
    try:
        rows = (
            db.query(ReferenceBuild)
            .options(joinedload(ReferenceBuild.parts).joinedload(ReferenceBuildPart.part))
            .filter(ReferenceBuild.is_active == True)
            .all()
        )
        return {row.build_key: _to_build(row) for row in rows}
    except Exception as e:
        logger.warning("DB query for reference builds failed, falling back to static data: %s", e)
        return dict(BUILDS)


def get_by_key(db: Session, build_key: str) -> tuple[str, Build] | None:
    try:
        row = (
            db.query(ReferenceBuild)
            .options(joinedload(ReferenceBuild.parts).joinedload(ReferenceBuildPart.part))
            .filter(ReferenceBuild.build_key == build_key, ReferenceBuild.is_active == True)
            .first()
        )
        if row is None:
            return BUILDS.get(build_key) and (build_key, BUILDS[build_key])
        return build_key, _to_build(row)
    except Exception as e:
        logger.warning("DB query for build key '%s' failed, falling back to static data: %s", build_key, e)
        build = BUILDS.get(build_key)
        return (build_key, build) if build else None


def _to_build(row: ReferenceBuild) -> Build:
    return Build(
        label=row.label,
        description=row.description,
        total_approx=row.total_approx,
        parts=[
            Part(
                component=rbp.component,
                brand=rbp.part.manufacturer or "",
                model=rbp.part.name,
                approx_price=rbp.approx_price,
            )
            for rbp in sorted(row.parts, key=lambda p: p.sort_order)
        ],
    )