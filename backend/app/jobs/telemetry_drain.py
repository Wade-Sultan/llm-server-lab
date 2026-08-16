"""Scheduled job entrypoint: `python -m app.jobs.telemetry_drain`.

Backstop for the build-telemetry write-behind buffer. In the normal case
turn_runner drains it at the end of every turn, so this finds nothing — its
reason to exist is the cases turn_runner cannot cover:

  - a worker that was SIGTERM'd between the recorder's buffer write and the
    drain at the end of its turn,
  - a period where Postgres was unreachable and every in-turn drain gave up
    leaving entries behind,
  - the tail of a backlog too large for the per-turn batch to clear.

Reuses the backend image; the CronJob only overrides the container command,
exactly like app.jobs.discovery and app.jobs.pricing_etl.

Costs no LLM tokens: this moves rows that were already paid for at build time.
"""

import asyncio
import logging

from app.core.logging import configure_logging
from app.services import telemetry_buffer
from app.services.recommender.recording import drain_pending

configure_logging()
logger = logging.getLogger(__name__)

# Bound on one run. Large enough to clear a real backlog in a single pass, small
# enough that a pathological buffer does not hold a transaction open all day.
_MAX_BATCHES = 50


async def run() -> int:
    """Returns a process exit code."""
    depth = await telemetry_buffer.count_pending()
    if depth is None:
        logger.error("telemetry drain: valkey unavailable")
        return 1
    if depth == 0:
        logger.info("telemetry drain: nothing buffered")
        return 0

    logger.info("telemetry drain: %d session(s) buffered", depth)
    total = 0
    for _ in range(_MAX_BATCHES):
        written = await drain_pending()
        if written == 0:
            # Either the buffer is empty or the batch was entirely duplicates
            # that have now been trimmed. Either way there is no progress to be
            # made by looping harder — drain_pending never raises, so a real
            # failure has already been logged.
            break
        total += written

    remaining = await telemetry_buffer.count_pending()
    logger.info(
        "telemetry drain finished",
        extra={"persisted": total, "remaining": remaining},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
