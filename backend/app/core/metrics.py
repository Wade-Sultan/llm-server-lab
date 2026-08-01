"""Prometheus metrics for the builder service.

SCRAPED, NOT PUSHED. Google Managed Prometheus scrapes this endpoint via the
`PodMonitoring` resource in deploy/overlays/prod/podmonitoring.yaml; Grafana
Cloud then queries GMP at read time. Nothing here talks to Grafana directly.
See deploy/observability.md.

WHY A SEPARATE PORT. The metrics endpoint listens on METRICS_PORT, not on the
API port, and that port is deliberately absent from the Service. Both
HTTPRoutes in deploy/base/ have a rule with no `matches:`, which in Gateway API
means "match every path" — so a /metrics mounted on the API app would be served
straight to the public internet, publishing every route name, request count and
latency distribution the service has. GMP scrapes pod IPs directly and never
goes through the Service or the Gateway, so a port that exists only on the pod
is scrapeable while staying unroutable from outside.
"""

import logging
import os

from fastapi import FastAPI
from prometheus_client import start_http_server
from prometheus_fastapi_instrumentator import Instrumentator

logger = logging.getLogger(__name__)

METRICS_PORT = int(os.environ.get("METRICS_PORT", "9090"))


def instrument(app: FastAPI) -> None:
    """Attach request instrumentation to `app`.

    Collection only — this deliberately does not call `Instrumentator.expose()`,
    which would mount /metrics on the public API app. Serving is start_exporter's
    job, on its own port.
    """
    Instrumentator(
        # The health endpoints are hit by three probes on 5-15s periods, which
        # would otherwise dominate every request-rate panel and bury real
        # traffic. GMP's own scrape of /metrics is on the other port and never
        # reaches this app at all.
        excluded_handlers=["/api/v1/healthz", "/api/v1/readyz"],
        # THE IMPORTANT ONE FOR THIS SERVICE. /chat streams SSE for up to
        # _DSPY_CHAT_TIMEOUT_S (180s), and by default the duration histogram
        # measures until the stream *closes* — so a normal, healthy build
        # records as a 180-second request. Left at the default, /chat alone
        # would drag p95 into the minutes and make the latency panels useless
        # for spotting real regressions. True measures time-to-first-byte
        # instead, which for an SSE endpoint is the number that means anything.
        should_exclude_streaming_duration=True,
        # Concurrency gauge. Worth having here specifically because builder is
        # mostly blocked on OpenRouter rather than CPU — the HPA's CPU target
        # understates load (see overlays/prod/hpa.yaml), and in-flight request
        # count is the signal that would actually show saturation.
        should_instrument_requests_inprogress=True,
        inprogress_labels=True,
    ).instrument(app)
    # Cardinality is safe at the defaults and worth not changing: the `handler`
    # label is the route *template* ("/api/v1/listings/{id}"), and unmatched
    # paths collapse to a single "none" bucket rather than minting a series per
    # 404 a scanner probes.


def start_exporter() -> None:
    """Serve the default registry on METRICS_PORT in a background thread."""
    try:
        start_http_server(METRICS_PORT)
    except OSError:
        # Almost always "address already in use" from running more than one
        # uvicorn worker: prometheus_client's default registry is per-process,
        # so each worker would need its own port (or multiprocess mode). The
        # Dockerfile pins --workers 1, which is why this is a warning and not a
        # hard failure — losing metrics must not take the API down with it.
        logger.warning(
            "metrics exporter could not bind port %d; metrics are NOT being "
            "exported from this process. If --workers was raised above 1, "
            "prometheus_client needs multiprocess mode.",
            METRICS_PORT,
            exc_info=True,
        )
        return

    logger.info("metrics exporter listening on :%d/metrics", METRICS_PORT)
