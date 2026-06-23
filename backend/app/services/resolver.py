from sqlalchemy.ext.asyncio import AsyncSession
from app.crud.reference_builds import get_all_active
from app.data.refbuilds import Build
from app.schemas.chat import BuildProfile

RESOLUTION_FLOOR = {
    "1080p": 1080,
    "1440p": 1440,
    "4k":    2160,
}

async def resolve_build(profile: BuildProfile, db: AsyncSession) -> tuple[str, Build]:
    """Map a BuildProfile to the best matching pre-defined build key."""

    use = profile.primary_use
    resolution = profile.gaming_resolution or "1080p"
    budget = profile.budget_tier
    floor = RESOLUTION_FLOOR.get(resolution, 1080)

    # Filter candidates by resolution floor
    builds = await get_all_active(db)
    candidates = {
        key: build
        for key, build in builds.items()
        if key[:4].isdigit() and int(key[:4]) >= floor
    }

    # Local LLM / AI workloads
    if use == "local_llm":
        if budget == "elite" and floor >= 2160:
            if any("localllmpro" in k for k in candidates):
                return _pick(candidates, "2160_localllmpro")
            return _pick(candidates, "2160_localllm")
        if floor >= 2160:
            return _pick(candidates, "2160_localllm")
        return _pick(candidates, "1440_localllm")

    # Video editing / content creation
    if use == "video_editing":
        if floor >= 2160:
            return _pick(candidates, "2160_creator")
        return _pick(candidates, "1440_creator")

    # Gaming
    if use == "gaming":
        if floor >= 2160:
            return _pick(candidates, "2160_cinematic")
        if floor >= 1440:
            if budget == "entry" or budget == "mid":
                return _pick(candidates, "1440_mid")
            if budget == "high":
                return _pick(candidates, "1440_uppermid")
            if budget == "elite":
                return _pick(candidates, "1440_competitive")
        # 1080p
        if budget == "elite" or budget == "high":
            return _pick(candidates, "1080_competitive")
        return _pick(candidates, "1080_entry")

    # General / fallback
    return _pick(candidates, "1080_entry")


def _pick(candidates: dict, key: str) -> tuple[str, Build]:
    """Return the requested key if it passed the resolution filter, 
    otherwise fall back to the highest resolution candidate available."""
    if key in candidates:
        return key, candidates[key]
    # Fallback: highest resolution build available
    fallback_key = max(candidates.keys())
    return fallback_key, candidates[fallback_key]