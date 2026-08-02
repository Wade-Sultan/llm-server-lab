import asyncio
import logging
from contextlib import asynccontextmanager, suppress

import sentry_sdk
from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.middleware.cors import CORSMiddleware

from app.api.main import api_router
from app.core import pubsub
from app.core.config import settings
from app.core.loadtest import LoadTestMiddleware
from app.core.logging import configure_logging
from app.core.metrics import instrument as instrument_metrics
from app.core.metrics import start_exporter as start_metrics_exporter
from app.core.valkey import close_client as close_valkey
from app.core.warmup import mark_dspy_warm

# Before anything else logs, so uvicorn's startup lines are JSON too.
configure_logging()

logger = logging.getLogger(__name__)


def custom_generate_unique_id(route: APIRoute) -> str:
    return f"{route.tags[0]}-{route.name}"


if settings.SENTRY_DSN and settings.ENVIRONMENT != "local":
    sentry_sdk.init(dsn=str(settings.SENTRY_DSN), enable_tracing=True)


async def _warm_dspy_pipeline() -> None:
    """Import DSPy/litellm and configure the LM off the request path, so a
    cold-started instance can bind its port and start accepting connections
    immediately instead of blocking on this multi-second import chain."""
    try:
        from app.services.chat_pipeline import warm_dspy_pipeline

        await asyncio.to_thread(warm_dspy_pipeline)
    except Exception:
        logger.exception(
            "DSPy warm-up failed; it will be configured lazily on first /chat request instead."
        )
    finally:
        # Flip readiness either way — see mark_dspy_warm's docstring for why a
        # failed warm-up must not pin the pod out of the Service forever.
        mark_dspy_warm()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Started here rather than at import time so the port is only bound by the
    # process that actually serves, not by anything that merely imports
    # app.main (alembic, the test suite, `fastapi run`'s reloader parent).
    start_metrics_exporter()

    # Fire-and-forget: don't await, so lifespan startup (and thus port
    # binding) isn't blocked on the dspy/litellm import chain.
    warm_task = asyncio.create_task(_warm_dspy_pipeline())
    app.state.dspy_warm_task = warm_task
    try:
        yield
    finally:
        # A SIGTERM landing mid-cold-start leaves this task partway through the
        # import chain. Cancel and await it so shutdown isn't held open by it
        # and asyncio doesn't log "Task was destroyed but it is pending".
        if not warm_task.done():
            warm_task.cancel()
            with suppress(asyncio.CancelledError):
                await warm_task

        # Flushes anything the publisher has batched but not yet sent. Skipping
        # this drops turns that were accepted by /chat but never reached the
        # topic — the user watches a stream that no worker will ever write to.
        pubsub.close()
        await close_valkey()


app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    generate_unique_id_function=custom_generate_unique_id,
    lifespan=lifespan,
)

# Set all CORS enabled origins
if settings.all_cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.all_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# add_middleware, so this wraps outside the router but inside CORS. Registered
# as a raw ASGI class rather than @app.middleware("http") on purpose — the
# latter is BaseHTTPMiddleware, which runs the endpoint in a separate task and
# would lose the ContextVar this sets. See app/core/loadtest.py.
app.add_middleware(LoadTestMiddleware)

app.include_router(api_router, prefix=settings.API_V1_STR)

# After include_router: the instrumentator reads the route table to label
# timings by route template, so routes registered afterwards would be recorded
# under their raw path instead. Module level, not lifespan — adding middleware
# to a running app raises RuntimeError.
instrument_metrics(app)
