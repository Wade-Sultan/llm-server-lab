"""Scheduled job entrypoint: `python -m app.jobs.benchmarks`.

Fills `benchmark_scores` on CPUs and GPU chipsets that don't have them, so the
recommender's scorer has real numbers to rank candidates on.

WHY COVERAGE MATTERS MORE THAN IT LOOKS. services/recommender/scoring.py injects
a workload-weighted perf_score into every CPU and GPU candidate list, and its
dominance gate — the thing that lets a step skip its LLM call — refuses to fire
unless *every* candidate in the set is scored. One unmeasured chipset inside a
budget band therefore disables the bypass for every build that lands in that
band. Chasing coverage to 100% is what switches the saving on.

COSTS REAL MONEY per part: one Tavily search plus up to three fetch+extract
calls. DISCOVERY_BENCHMARK_LIMIT caps how many parts one run will attempt, and
the run is recorded in discovery_runs like any other, so the spend is visible
next to the sweeps rather than hidden in a cron log.

Idempotent by construction — it only ever selects rows whose benchmark_scores
is NULL or {}, so re-running costs nothing once coverage is complete.
"""

import asyncio
import logging
import os

from app.core.logging import configure_logging
from app.services.discovery.benchmarks import backfill_benchmarks
from app.services.discovery.search import DiscoveryConfigError

configure_logging()
logger = logging.getLogger(__name__)

# Which categories to backfill, in order. Both by default.
_CATEGORIES = [
    c.strip()
    for c in (os.getenv("DISCOVERY_BENCHMARK_CATEGORIES") or "cpu,gpu_chipset").split(
        ","
    )
    if c.strip()
]

# Parts attempted per category per run — the spend dial, same role as
# DISCOVERY_SWEEP_MAX_CANDIDATES on the discovery side.
_LIMIT = int(os.getenv("DISCOVERY_BENCHMARK_LIMIT", "25"))


async def run() -> int:
    """Returns a process exit code."""
    total_enriched = 0
    try:
        for category in _CATEGORIES:
            stats = await backfill_benchmarks(category, limit=_LIMIT)
            total_enriched += stats.enriched
            logger.info(
                "benchmark backfill finished",
                extra={
                    "category": category,
                    "considered": stats.considered,
                    "enriched": stats.enriched,
                    "no_sources": stats.no_sources,
                    "nothing_extracted": stats.nothing_extracted,
                    "cost_usd": str(stats.cost_usd),
                },
            )
    except DiscoveryConfigError:
        # A missing TAVILY_API_KEY is a deployment error, and every run will
        # fail identically until it is fixed — worth a red CronJob.
        logger.exception("benchmark backfill: search is not configured")
        return 1
    except ValueError:
        logger.exception(
            "benchmark backfill: DISCOVERY_BENCHMARK_CATEGORIES contains an "
            "unsupported category (valid: cpu, gpu_chipset)"
        )
        return 1

    logger.info("benchmark backfill: %d part(s) enriched", total_enriched)
    # Parts that found no usable source are left unscored and will be retried
    # next run, so an incomplete pass is the normal steady state rather than a
    # failure.
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
