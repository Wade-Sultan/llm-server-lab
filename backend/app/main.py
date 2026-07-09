import asyncio
import logging
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.middleware.cors import CORSMiddleware

from app.api.main import api_router
from app.core.config import settings

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fire-and-forget: don't await, so lifespan startup (and thus port
    # binding) isn't blocked on the dspy/litellm import chain.
    app.state.dspy_warm_task = asyncio.create_task(_warm_dspy_pipeline())
    yield


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

app.include_router(api_router, prefix=settings.API_V1_STR)
