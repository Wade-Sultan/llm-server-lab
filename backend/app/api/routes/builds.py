"""Public, read-only access to shared build snapshots.

Both endpoints are token-addressed and unauthenticated by design: the token is
the capability (see models/shared_build.py), and the snapshot deliberately
contains nothing about the conversation or the user — the `profile` was
stripped before it was stored.
"""

import logging
import re

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import select

from app.core.config import settings
from app.core.db import AsyncSessionLocal
from app.models.shared_build import SharedBuild
from app.services.build_pdf import render_build_pdf

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/builds", tags=["builds"])

# token_urlsafe alphabet. Rejecting anything else up front keeps garbage out of
# the query and makes the 404 cheap.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,32}$")


async def _get_shared_build(token: str) -> SharedBuild:
    if not _TOKEN_RE.match(token):
        raise HTTPException(status_code=404, detail="Build not found.")
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(SharedBuild).where(SharedBuild.token == token)
        )
        row = result.scalars().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Build not found.")
    return row


def share_url(token: str) -> str:
    return f"{settings.FRONTEND_HOST}/b/{token}"


@router.get("/{token}")
async def get_build(token: str) -> dict:
    row = await _get_shared_build(token)
    return {
        "token": row.token,
        "build": row.build,
        "created_at": row.created_at.isoformat(),
    }


@router.get("/{token}/pdf")
async def get_build_pdf(token: str) -> Response:
    row = await _get_shared_build(token)
    pdf_bytes = render_build_pdf(row.build, share_url(row.token), row.created_at)
    label = (row.build.get("label") or "build").strip().replace(" ", "-").lower()
    # Sanitized to keep header injection out of Content-Disposition.
    filename = re.sub(r"[^a-z0-9-]", "", label) or "build"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}-{row.token}.pdf"',
            # Immutable by construction — the snapshot never changes.
            "Cache-Control": "public, max-age=86400",
        },
    )
