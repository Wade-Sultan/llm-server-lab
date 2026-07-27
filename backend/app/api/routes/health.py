import logging

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.core.db import async_engine
from app.core.warmup import is_dspy_warm

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health", tags=["health"])
def health():
    return {"status": "ok", "message": "backend ok"}


@router.get("/healthz", tags=["health"])
def healthz():
    """Liveness. Deliberately does no I/O — a Cloud SQL blip must restart
    nothing, and this is also the startupProbe target while DSPy imports."""
    return {"status": "ok"}


@router.get("/readyz", tags=["health"])
async def readyz(response: Response):
    """Readiness: take traffic only once the pool answers and the DSPy
    warm-up has finished. Without the warm gate, a pod added to the Service
    mid-import makes some unlucky user pay the whole import cost."""
    checks = {"database": False, "dspy_warm": is_dspy_warm()}

    try:
        async with async_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception:
        logger.warning("readiness check: database unreachable", exc_info=True)

    if all(checks.values()):
        return {"status": "ready", "checks": checks}

    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "not_ready", "checks": checks}
