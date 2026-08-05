from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.reference_builds import get_all_active
from app.data.refbuilds import Build
from app.schemas.chat import BuildProfile

RESOLUTION_FLOOR = {
    "1080p": 1080,
    "1440p": 1440,
    "4k": 2160,
}


async def resolve_build(profile: BuildProfile, db: AsyncSession) -> tuple[str, Build]:
    """Map a BuildProfile to the best matching pre-defined build key."""

    use = profile.primary_use
    resolution = profile.gaming_resolution or "1080p"
    # 'custom' (no budget ceiling) collapses to 'elite' here. The reference
    # catalog is a fixed set of curated builds with a top entry — there is no
    # "unlimited" one to resolve to, and the branches below are written as an
    # ordered ladder that an unrecognised tier would fall straight through.
    # Only the DSPy path can actually spend an unbounded budget; this is its
    # fallback, so the top rung is the right answer.
    budget = "elite" if profile.budget_tier == "custom" else profile.budget_tier
    floor = RESOLUTION_FLOOR.get(resolution, 1080)

    # Filter candidates by resolution floor
    builds = await get_all_active(db)
    candidates = {
        key: build
        for key, build in builds.items()
        if build.get("max_resolution") is not None and build["max_resolution"] >= floor
    }

    # Server / workstation builds. Reference builds are the fallback when the
    # DSPy pipeline fails, so a server profile landing on a gaming build would
    # be a worse failure than most — it would recommend a machine that cannot
    # do the job at all. Keys follow the same "<resolution>_<name>" convention
    # as the rest of the catalog; a server build's max_resolution is nominal
    # (it exists to pass the resolution filter, not to describe a display).
    if use == "server":
        if budget in ("high", "elite"):
            return _pick(candidates, "2160_serverpro")
        return _pick(candidates, "2160_server")

    # AI workloads (inference, training, image gen)
    if use == "ai":
        if budget == "elite" and floor >= 2160:
            if any("localllmpro" in k for k in candidates):
                return _pick(candidates, "2160_localllmpro")
            return _pick(candidates, "2160_localllm")
        if floor >= 2160:
            return _pick(candidates, "2160_localllm")
        return _pick(candidates, "1440_localllm")

    # Video editing / 3D rendering / content creation
    if use in ("video_editing", "3d_rendering"):
        if floor >= 2160:
            return _pick(candidates, "2160_creator")
        return _pick(candidates, "1440_creator")

    # Gaming (streaming rides the gaming builds — the CPU-heavier balance is
    # handled by the DSPy pipeline's budget split, not the reference catalog)
    if use in ("gaming", "streaming"):
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
    fallback_key = max(candidates, key=lambda k: candidates[k]["max_resolution"])
    return fallback_key, candidates[fallback_key]
