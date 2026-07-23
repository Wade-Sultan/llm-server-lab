from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import pricing_etl as crud

# SerpAPI's plan cap. Kept as a module constant rather than a Settings field —
# same call as discovery/runner.py's _MAX_SOURCES, this isn't something that
# needs per-environment tuning.
SEARCH_BUDGET_MONTHLY = 1000

# Once monthly usage crosses this fraction of budget, low-priority part types
# (cpu coolers, cases, PSUs, fans — see runner.LOW_PRIORITY_TYPES) stop being
# checked for the rest of the month, so the remaining budget goes to the
# higher-value types (CPU/GPU/motherboard/RAM/storage).
LOW_PRIORITY_CUTOFF_PCT = 0.8


def _current_month() -> date:
    return date.today().replace(day=1)


async def get_remaining(db: AsyncSession) -> int:
    used = await crud.get_quota_used(db, _current_month())
    return max(0, SEARCH_BUDGET_MONTHLY - used)


async def low_priority_allowed(db: AsyncSession) -> bool:
    used = await crud.get_quota_used(db, _current_month())
    return used < SEARCH_BUDGET_MONTHLY * LOW_PRIORITY_CUTOFF_PCT


async def record_search(db: AsyncSession) -> None:
    await crud.record_search(db, _current_month())
