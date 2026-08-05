"""Scheduled job entrypoint: `python -m app.jobs.ai_models`.

Monthly Hugging Face Hub sweep. Stages whatever the Hub is currently trending
that the ai_models catalog doesn't already have, into the same review queue the
hardware categories use — nothing reaches ai_models without a human approving
it in the admin panel.

Reuses the backend image; the CronJob only overrides the container command,
exactly like app.jobs.discovery and app.jobs.pricing_etl.

Unlike the hardware jobs this costs no LLM tokens at all: the Hub publishes
these fields as structured JSON, so there is nothing to extract. The bound in
DISCOVERY_SWEEP_MAX_CANDIDATES still applies, but as a cap on how much a
reviewer is handed at once rather than on spend.
"""

import asyncio
import logging
import os

from app.core.logging import configure_logging
from app.services.discovery.huggingface import HuggingFaceError
from app.services.discovery.runner import run_ai_model_sweep

configure_logging()
logger = logging.getLogger(__name__)

# Optional Hub search term, e.g. "gguf" or "qwen". Unset sweeps all of trending,
# which is the intended default for the scheduled run.
_HINT = os.getenv("DISCOVERY_AI_MODEL_HINT") or None


async def run() -> int:
    """Returns a process exit code."""
    try:
        run_id = await run_ai_model_sweep(_HINT)
    except HuggingFaceError:
        logger.exception("ai-model sweep: Hugging Face Hub unreachable")
        return 1

    logger.info("ai-model sweep finished", extra={"run_id": str(run_id)})
    # _sweep_ai_models never raises and records its own outcome on the run row,
    # so reaching here means the job did its work — a non-zero exit would only
    # make the CronJob repeat a sweep that already staged its items.
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
