"""Cloud Run Job / CronJob entrypoint: `python -m app.jobs.listing_failure_digest`.

Asks commerce to mail the operator about parts the listings API could not
produce a listing for, then exits. Reuses the backend's own Docker image — no
separate Dockerfile — the Job resource just overrides the container's
command/args, exactly like the pricing ETL.

This job holds no logic of its own on purpose. Commerce records the failures,
owns the email credential and the templates, and reads the same database, so it
does the whole thing; what it cannot do is decide *when*, because it runs more
than one replica and an in-process timer would send one digest per pod. That
decision is this job, and the CronJob schedule behind it.
"""

import asyncio
import logging
import sys

from app.services import commerce_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def run() -> int:
    """Returns a process exit code. Nothing to report is a success: it is the
    healthy steady state, and a red job every morning that the catalog is fine
    would train exactly the wrong reflex."""
    if not commerce_client.is_configured():
        logger.error(
            "COMMERCE_INTERNAL_URL and COMMERCE_INTERNAL_KEY must both be set "
            "to send the listing failure digest"
        )
        return 1

    try:
        result = await commerce_client.trigger_listing_failure_digest()
    except commerce_client.CommerceError:
        # Non-zero so the CronJob's failure shows up in the cluster. The next
        # run picks up exactly the same rows: nothing is marked reported unless
        # the email actually went out.
        logger.exception("listing failure digest failed")
        return 1

    status = result.get("status")
    if status == "nothing_to_report":
        logger.info("listing failure digest: nothing new to report")
    else:
        logger.info(
            "listing failure digest sent: %s reported, %s open in total",
            result.get("reported"),
            result.get("open"),
        )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
