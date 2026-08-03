"""Scheduled job entrypoint: `python -m app.jobs.discovery`.

Gap-fill pass over the review queue. Picks up pending discovered_items whose
last extraction failed validation and re-runs discovery for each, giving the
extractor a fresh set of sources. The partial unique index
(uq_discovered_items_pending_cat_name) means a re-run refreshes the existing
pending row via ON CONFLICT DO UPDATE rather than stacking duplicates.

Reuses the backend image; the CronJob only overrides the container command,
exactly like app.jobs.pricing_etl.

Bounded by DISCOVERY_MAX_ITEMS because every item costs LLM calls: three
sources fetched and extracted per item, billed through OpenRouter. Items are
processed one at a time — _run already fans out across sources internally, and
serialising at this level keeps search-API rate limits and spend predictable.
"""

import asyncio
import logging
import os

from app.core.db import AsyncSessionLocal
from app.core.logging import configure_logging
from app.crud import discovery as crud
from app.services.discovery.runner import run_discovery
from app.services.discovery.search import DiscoveryConfigError

configure_logging()
logger = logging.getLogger(__name__)

_MAX_ITEMS = int(os.getenv("DISCOVERY_MAX_ITEMS", "25"))


async def run() -> int:
    """Returns a process exit code."""
    async with AsyncSessionLocal() as db:
        targets = await crud.get_incomplete_items(db, limit=_MAX_ITEMS)

    if not targets:
        logger.info(
            "discovery gap-fill: nothing to do", extra={"max_items": _MAX_ITEMS}
        )
        return 0

    logger.info(
        "discovery gap-fill starting",
        extra={"item_count": len(targets), "max_items": _MAX_ITEMS},
    )

    succeeded = 0
    failed = 0
    for query, category in targets:
        try:
            run_id = await run_discovery(query, category)
        except DiscoveryConfigError:
            # Missing/invalid search API key: every remaining item would fail
            # the same way, so stop rather than burn the whole queue.
            logger.exception("discovery gap-fill: configuration error, aborting")
            return 1
        except Exception:
            # _run swallows its own per-item failures into the run row; this
            # only catches something escaping that, which must not take the
            # rest of the batch down with it.
            failed += 1
            logger.exception(
                "discovery gap-fill: item failed",
                extra={"query": query, "category": category},
            )
        else:
            succeeded += 1
            logger.info(
                "discovery gap-fill: item complete",
                extra={"query": query, "category": category, "run_id": str(run_id)},
            )

    logger.info(
        "discovery gap-fill finished",
        extra={"succeeded": succeeded, "failed": failed},
    )
    # Partial failures exit 0 on purpose: a non-zero exit makes the CronJob
    # retry the entire batch, re-paying for the items that already succeeded.
    # Per-item outcomes are recorded on their discovery_runs rows.
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
