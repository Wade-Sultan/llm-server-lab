"""OpenTelemetry tracing: one SDK, two destinations.

    spans ──┬─► OTLP to $OTEL_EXPORTER_OTLP_ENDPOINT ──► Google Cloud Observability
            │   (Managed OpenTelemetry for GKE injects that variable)
            └─► OTLP to LangSmith, scope-filtered ─────► LangSmith

WHY TWO PIPES RATHER THAN A COLLECTOR FAN-OUT. Managed OpenTelemetry for GKE
cannot export to third-party backends — it writes to Google Cloud Observability
and nowhere else — and it owns OTEL_EXPORTER_OTLP_ENDPOINT in the pod, so it
cannot be pointed at LangSmith either. Reaching both means either self-hosting a
collector that fans out, or attaching a second span processor here. This is the
second, which costs no infrastructure to run.

WHY THE LANGSMITH PIPE IS FILTERED. Everything the process traces goes to Google:
FastAPI request spans, Cloud SQL spans, Pub/Sub spans. LangSmith is an LLM
observability tool billed by what you send it, and a chat turn's HTTP span tells
it nothing it can use. So only spans from the LLM and graph instrumentation
scopes take that exit. Cloud Trace still sees all of them.

EVERY PIECE IS INDEPENDENTLY OPTIONAL. No OTLP endpoint, no Google pipe. No
LangSmith key, no LangSmith pipe. Neither configured, and configure_tracing() is
a no-op that leaves the global provider alone. Local development and the test
suite hit that last case, and must keep working exactly as they did.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Instrumentation scopes whose spans are worth sending to LangSmith. Matched as
# prefixes, because these libraries version their scope names ("langsmith",
# "langsmith.client", "opentelemetry.instrumentation.openai_v2" and so on) and
# pinning exact strings would silently stop matching on an upgrade — a failure
# that looks like "tracing just stopped working" with nothing in the logs.
_LLM_SCOPE_PREFIXES = (
    "langsmith",
    "langchain",
    "langgraph",
    "opentelemetry.instrumentation.openai",
    "litellm",
    "dspy",
)

_provider = None


def _is_llm_scope(name: str | None) -> bool:
    return bool(name) and name.startswith(_LLM_SCOPE_PREFIXES)  # type: ignore[union-attr]


def _build_scope_filter_processor(inner):
    """Wrap a span processor so only LLM/graph spans reach it.

    Defined inside a function so the opentelemetry.sdk import stays off the
    module's import path — this module is imported by main.py at startup, and
    the SDK is not needed at all when tracing is unconfigured.
    """
    from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor

    class ScopeFilterSpanProcessor(SpanProcessor):
        """Forwards only spans emitted by an LLM/graph instrumentation scope.

        Filtering on end rather than on start: a span's scope is known at both
        points, but dropping at on_start would also drop the matching on_end and
        leave the wrapped BatchSpanProcessor with an unbalanced view. on_start
        is forwarded unconditionally and is cheap — the batch processor does
        nothing with it.
        """

        def __init__(self, wrapped: SpanProcessor) -> None:
            self._wrapped = wrapped

        def on_start(self, span, parent_context=None) -> None:
            self._wrapped.on_start(span, parent_context)

        def on_end(self, span: ReadableSpan) -> None:
            scope = getattr(span, "instrumentation_scope", None)
            if _is_llm_scope(getattr(scope, "name", None)):
                self._wrapped.on_end(span)

        def shutdown(self) -> None:
            self._wrapped.shutdown()

        def force_flush(self, timeout_millis: int = 30_000) -> bool:
            return self._wrapped.force_flush(timeout_millis)

    return ScopeFilterSpanProcessor(inner)


def _instrument_infra(fastapi_app) -> None:
    """Attach span emission to the non-LLM half of a turn.

    These are the spans the scope filter deliberately keeps out of LangSmith:
    the inbound request, the SQL it runs, the Valkey checkpoint reads, the
    outbound HTTP. They exist for Cloud Trace, and they are the reason the
    Google pipe is worth turning on at all — without them it carries the same
    LLM spans LangSmith already has and nothing else.

    Managed OpenTelemetry for GKE does not supply any of this. It runs a
    collector and injects OTEL_EXPORTER_OTLP_ENDPOINT; instrumenting the
    process is still the process's job.

    Each instrumentor is caught separately — a missing optional dependency or
    a version skew in one should not cost the others.
    """
    if fastapi_app is not None:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            # instrument_app, not the global instrument(): configure_tracing runs
            # from the lifespan hook, which is long after app.main built the
            # FastAPI object, and the global form only patches instances
            # constructed after it is called.
            FastAPIInstrumentor.instrument_app(fastapi_app)
        except Exception:
            logger.warning("FastAPI OTel instrumentation failed", exc_info=True)

    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        SQLAlchemyInstrumentor().instrument()
    except Exception:
        logger.warning("SQLAlchemy OTel instrumentation failed", exc_info=True)

    try:
        from opentelemetry.instrumentation.redis import RedisInstrumentor

        RedisInstrumentor().instrument()
    except Exception:
        logger.warning("Redis/Valkey OTel instrumentation failed", exc_info=True)

    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        # Catches ChatOpenRouter too, which reaches OpenRouter over a bare httpx
        # client rather than the openai SDK. Those spans carry no prompt or
        # token counts — they are transport timing, which is what Cloud Trace
        # wants and what the LangSmith scope filter is right to drop.
        HTTPXClientInstrumentor().instrument()
    except Exception:
        logger.warning("httpx OTel instrumentation failed", exc_info=True)


def _instrument_llm_clients(langsmith_enabled: bool) -> None:
    """Attach span emission to the LLM calls LangChain does not already cover.

    The chat pipeline's own calls go through ChatOpenRouter and are traced by
    LangChain itself — nothing to do for those. What is left is the recommender:
    DSPy's ten build steps reach OpenRouter through litellm, and they are where
    most of a completed build's spend goes, so leaving them out would make the
    per-conversation total in LangSmith an understatement rather than a number.

    Each failure is caught separately; losing the DSPy spans should not also
    lose the openai ones.
    """
    try:
        from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor

        OpenAIInstrumentor().instrument()
    except Exception:
        logger.warning("openai OTel instrumentation failed", exc_info=True)

    try:
        import litellm

        # Appended, not assigned — DSPy installs its own callbacks and
        # overwriting them would break its LM history, which
        # app/services/recommender/recording.py reads for cost capture.
        if "otel" not in litellm.callbacks:
            litellm.callbacks.append("otel")

        # Distinct from the OTel callback above and not redundant with it: the
        # OTel one produces spans, this one produces LangSmith *runs* with token
        # counts attached, which is what the spend view actually reads. Only
        # when a key is configured, since litellm would otherwise fail per call
        # trying to post them.
        if langsmith_enabled and "langsmith" not in litellm.callbacks:
            litellm.callbacks.append("langsmith")
    except Exception:
        logger.warning("litellm callback setup failed", exc_info=True)


def configure_tracing(service_name: str, fastapi_app=None) -> None:
    """Install the tracer provider for this process. Idempotent.

    service_name distinguishes the API pod from the worker pod in both
    backends — they run the same image and the same pipeline, so without it a
    trace gives no clue which one produced it.

    fastapi_app is the object to attach request instrumentation to; the worker
    has no such object and passes nothing.
    """
    global _provider

    if _provider is not None:
        return

    from app.core.config import settings
    from app.core.loadtest import is_load_test

    if is_load_test():
        # A load test runs thousands of stubbed turns. Tracing them would ship
        # thousands of traces describing calls that never happened.
        logger.info("load test in progress; tracing not configured")
        return

    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    langsmith_key = settings.LANGSMITH_API_KEY

    if not otlp_endpoint and not langsmith_key:
        logger.info(
            "tracing not configured (no OTEL_EXPORTER_OTLP_ENDPOINT, no "
            "LANGSMITH_API_KEY); spans are not being exported"
        )
        return

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    from app.services.recommender.dspy_pipeline import PIPELINE_VERSION

    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": service_name,
                "service.version": PIPELINE_VERSION,
                "deployment.environment": settings.ENVIRONMENT,
            }
        )
    )

    if otlp_endpoint:
        # No endpoint argument: the exporter reads OTEL_EXPORTER_OTLP_ENDPOINT
        # itself, along with the rest of the standard OTEL_* knobs. Managed OTel
        # for GKE sets those through its Instrumentation CR, and hardcoding an
        # endpoint here would override an injection that is meant to win.
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        logger.info("tracing: exporting to OTLP endpoint %s", otlp_endpoint)

    if langsmith_key:
        langsmith_exporter = OTLPSpanExporter(
            endpoint=f"{settings.LANGSMITH_OTEL_ENDPOINT.rstrip('/')}/v1/traces",
            headers={
                "x-api-key": langsmith_key,
                "Langsmith-Project": settings.LANGSMITH_PROJECT,
            },
        )
        provider.add_span_processor(
            _build_scope_filter_processor(BatchSpanProcessor(langsmith_exporter))
        )
        # Attaches LangChain's tracer to every chain, graph node and model call.
        # Without it LangChain never instruments anything and the LangSmith pipe
        # below carries only the DSPy/litellm spans — ChatOpenRouter subclasses
        # BaseChatModel over raw httpx, so the openai instrumentor does not see
        # the chat pipeline either, and the graph goes entirely untraced.
        os.environ["LANGSMITH_TRACING"] = "true"
        # Makes LangGraph/LangChain emit OTel spans instead of posting to
        # LangSmith's own REST API. Set here rather than in the environment so
        # it cannot be true while this provider is absent, which would send the
        # graph's spans nowhere at all.
        #
        # LANGSMITH_TRACING_MODE, not the older LANGSMITH_OTEL_ENABLED: that one
        # is now a legacy alias resolving to "hybrid", which keeps the REST
        # posts *as well as* the spans and bills LangSmith for each run twice.
        os.environ["LANGSMITH_TRACING_MODE"] = "otel"
        logger.info(
            "tracing: exporting LLM spans to LangSmith project %r",
            settings.LANGSMITH_PROJECT,
        )

    trace.set_tracer_provider(provider)
    _provider = provider

    _instrument_llm_clients(langsmith_enabled=bool(langsmith_key))
    _instrument_infra(fastapi_app)


def shutdown_tracing() -> None:
    """Flush and stop the exporters. Idempotent.

    Matters most in the worker: turns are short, spans are batched, and SIGTERM
    arrives without warning, so without this the last turn before a rollout
    routinely disappears from both backends.
    """
    global _provider
    if _provider is None:
        return
    provider, _provider = _provider, None
    try:
        provider.shutdown()
    except Exception:
        logger.debug("tracing shutdown failed (exiting anyway)", exc_info=True)


# ---------------------------------------------------------------------------
# Run metadata — the join key between LangSmith and our own telemetry
# ---------------------------------------------------------------------------
#
# LangSmith's OTel ingestion promotes span attributes named
# `langsmith.metadata.<key>` into a run's metadata, and groups runs into Threads
# on the metadata keys `thread_id` / `session_id` / `conversation_id`. Without
# one of those set, every chat turn arrives as an isolated trace and no
# multi-turn question ("how many turns to a build?", "did they abandon?") can be
# asked at all — which is the whole reason this exists.
#
# Several aliases are written rather than one, because which key LangSmith
# groups on has changed across versions and writing three costs nothing.
#
# THE JOIN. `build_session_id` is not a LangSmith concept; it is the primary key
# of our own `build_sessions` row. Putting it in run metadata is what lets a bad
# trace in LangSmith be traced back to the exact candidate sets and chosen parts
# in `module_decisions`, and — with conversation_id on both sides — back again.
_THREAD_METADATA_ALIASES = ("thread_id", "session_id", "conversation_id")


def attach_run_metadata(**values: object) -> None:
    """Attach metadata to the current span, for LangSmith to promote onto the run.

    A no-op when tracing is unconfigured or no span is recording, so call sites
    need no guard of their own. Never raises: losing a metadata tag must not
    fail a chat turn.
    """
    try:
        from opentelemetry import trace as _trace

        span = _trace.get_current_span()
        if span is None or not span.is_recording():
            return
        for key, value in values.items():
            if value is None:
                continue
            span.set_attribute(f"langsmith.metadata.{key}", str(value))
    except Exception:  # pragma: no cover - defensive
        logger.debug("attach_run_metadata failed", exc_info=True)


def attach_thread(conversation_id: object) -> None:
    """Mark the current span as belonging to a conversation Thread.

    Writes every alias LangSmith has used for thread grouping — see
    _THREAD_METADATA_ALIASES.
    """
    if conversation_id is None:
        return
    attach_run_metadata(**{k: conversation_id for k in _THREAD_METADATA_ALIASES})
