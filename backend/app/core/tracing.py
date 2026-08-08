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


def configure_tracing(service_name: str) -> None:
    """Install the tracer provider for this process. Idempotent.

    service_name distinguishes the API pod from the worker pod in both
    backends — they run the same image and the same pipeline, so without it a
    trace gives no clue which one produced it.
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
        # Makes LangGraph/LangChain emit OTel spans instead of posting to
        # LangSmith's own REST API. Set here rather than in the environment so
        # it cannot be true while this provider is absent, which would send the
        # graph's spans nowhere at all.
        os.environ["LANGSMITH_OTEL_ENABLED"] = "true"
        logger.info(
            "tracing: exporting LLM spans to LangSmith project %r",
            settings.LANGSMITH_PROJECT,
        )

    trace.set_tracer_provider(provider)
    _provider = provider

    _instrument_llm_clients(langsmith_enabled=bool(langsmith_key))


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
