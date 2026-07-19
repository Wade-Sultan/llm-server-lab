import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_async_db

SessionDep = Annotated[AsyncSession, Depends(get_async_db)]


def require_admin_key(
    x_admin_key: Annotated[str | None, Header()] = None,
) -> None:
    """Shared-secret gate for admin-triggered endpoints (parts discovery).

    The admin panel's Next.js server actions hold DISCOVERY_API_KEY server-side
    and proxy calls with an X-Admin-Key header, so the secret never reaches a
    browser. There is no per-user admin auth on the FastAPI side yet."""
    if not settings.DISCOVERY_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="DISCOVERY_API_KEY is not configured on this server",
        )
    if not x_admin_key or not secrets.compare_digest(
        x_admin_key, settings.DISCOVERY_API_KEY
    ):
        raise HTTPException(status_code=403, detail="Invalid or missing X-Admin-Key")


AdminKeyDep = Depends(require_admin_key)
