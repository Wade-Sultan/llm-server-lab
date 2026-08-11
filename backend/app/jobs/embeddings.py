"""Scheduled job entrypoint: `python -m app.jobs.embeddings`.

Reconciles the `embeddings` table against the catalog and parts tables: embeds
anything new, re-embeds anything whose descriptive text changed, and deletes
vectors whose entity is gone.

WHY A SCHEDULED SWEEP RATHER THAN A WRITE HOOK. Rows reach this database from
the discovery pipeline, the admin app's Prisma writes, and hand-run SQL, and
only the first goes through the backend at all. A create hook would cover that
one path and silently miss the others. Reconciliation is content-addressed
(see services/embeddings/store.py), so it picks up every write no matter who
made it.

CHEAP WHEN IDLE, which is what makes a frequent schedule reasonable: an
unchanged catalog costs one indexed SELECT per entity type and zero API calls.
Cost scales with churn, not with catalog size.

Runs to completion and exits, reusing the backend image with an overridden
command — same shape as app.jobs.discovery and app.jobs.ai_models.
"""

import asyncio
import logging
import os

from app.core.db import AsyncSessionLocal
from app.core.logging import configure_logging
from app.models.embeddings import EmbeddedEntity
from app.services.embeddings import client, store

configure_logging()
logger = logging.getLogger(__name__)

# Comma-separated EmbeddedEntity values to restrict the sweep, e.g.
# "game,software,ai_model" to refresh only the catalog side. Unset sweeps
# everything, which is the intended default.
_TYPES = os.getenv("EMBEDDING_RECONCILE_TYPES") or ""

# Cap on rows embedded per type per run. Unset means no cap — correct for the
# steady state, where a run has a handful of changes to make. Set it for the
# first backfill of a large catalog if you want to spread the initial spend
# across several runs rather than paying it in one.
_LIMIT = os.getenv("EMBEDDING_RECONCILE_LIMIT")


def _requested_types() -> list[EmbeddedEntity] | None:
    if not _TYPES.strip():
        return None
    valid = {e.value: e for e in EmbeddedEntity}
    requested = []
    for raw in _TYPES.split(","):
        name = raw.strip()
        if not name:
            continue
        entity = valid.get(name)
        if entity is None:
            logger.warning(
                "ignoring unknown entity type %r in EMBEDDING_RECONCILE_TYPES "
                "(valid: %s)",
                name,
                ", ".join(sorted(valid)),
            )
            continue
        requested.append(entity)
    return requested or None


async def run() -> int:
    """Returns a process exit code."""
    if not client.is_configured():
        # Exit non-zero: an unconfigured key is a deployment error, not a quiet
        # no-op, and a green CronJob would hide it indefinitely.
        logger.error(
            "OPENAI_API_KEY is not set — embeddings cannot be generated and "
            "semantic catalog matching stays disabled"
        )
        return 1

    limit = int(_LIMIT) if _LIMIT and _LIMIT.isdigit() else None

    async with AsyncSessionLocal() as db:
        stats = await store.reconcile(db, _requested_types(), limit_per_type=limit)

    logger.info(
        "embedding reconcile finished",
        extra={
            "scanned": stats.scanned,
            "embedded": stats.embedded,
            "unchanged": stats.unchanged,
            "failed": stats.failed,
            "orphans_deleted": stats.orphans_deleted,
            "tokens": stats.tokens,
        },
    )
    # Individual failures are already retried by the next run (a failed row is
    # simply left un-upserted, so it still looks missing), so partial failure is
    # not a reason to fail the job and have the CronJob repeat the whole sweep.
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
