import uuid

from fastapi import APIRouter, HTTPException

from app.api.deps import AdminKeyDep, SessionDep
from app.core.config import settings
from app.crud import discovery as crud_discovery
from app.schemas.discovery import (
    DiscoveryRunDetailOut,
    DiscoverySweepRequest,
    DiscoveryTriggerRequest,
    DiscoveryTriggerResponse,
)
from app.services.discovery.runner import start_discovery_run, start_sweep_run

router = APIRouter(tags=["discovery"])


@router.post(
    "/discovery/runs",
    status_code=202,
    dependencies=[AdminKeyDep],
    response_model=DiscoveryTriggerResponse,
)
async def trigger_discovery_run(
    payload: DiscoveryTriggerRequest,
) -> DiscoveryTriggerResponse:
    # Fail before creating a run row — a run that can't search is noise.
    if not settings.TAVILY_API_KEY:
        raise HTTPException(
            status_code=503, detail="TAVILY_API_KEY is not configured on this server"
        )
    run_id = await start_discovery_run(payload.query, payload.category)
    return DiscoveryTriggerResponse(run_id=run_id, status="running")


@router.post(
    "/discovery/sweeps",
    status_code=202,
    dependencies=[AdminKeyDep],
    response_model=DiscoveryTriggerResponse,
)
async def trigger_discovery_sweep(
    payload: DiscoverySweepRequest,
) -> DiscoveryTriggerResponse:
    """Enumerate what's new in a category and discover each candidate.

    Separate endpoint rather than a mode flag on /discovery/runs: that one's
    `query` is a required part name, and a sweep has no part name at all.
    Polled through the same GET — a sweep is one run row carrying N items.
    """
    if not settings.TAVILY_API_KEY:
        raise HTTPException(
            status_code=503, detail="TAVILY_API_KEY is not configured on this server"
        )
    run_id = await start_sweep_run(payload.hint, payload.category)
    return DiscoveryTriggerResponse(run_id=run_id, status="running")


@router.get(
    "/discovery/runs/{run_id}",
    dependencies=[AdminKeyDep],
    response_model=DiscoveryRunDetailOut,
)
async def get_discovery_run(run_id: uuid.UUID, db: SessionDep) -> DiscoveryRunDetailOut:
    run = await crud_discovery.get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Discovery run not found")
    return run
