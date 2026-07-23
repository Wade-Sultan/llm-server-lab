"""Cloud Run Job entrypoint: `python -m app.jobs.pricing_etl`.

Runs one pricing ETL pass (see app/services/pricing_etl/runner.py) and exits.
Reuses the backend's own Docker image — no separate Dockerfile — the Cloud Run
Job resource just overrides the container's command/args.
"""

import asyncio
import logging

from app.services.pricing_etl.runner import run

logging.basicConfig(level=logging.INFO)


if __name__ == "__main__":
    asyncio.run(run())
