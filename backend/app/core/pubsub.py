"""Pub/Sub publisher for chat turn requests.

WHAT CROSSES THIS BOUNDARY. Only a request to run a turn — never the turn's
output. Events go to Valkey (app/services/turn_stream.py), because Pub/Sub cannot
deliver to a browser and its at-least-once redelivery would duplicate tokens
mid-sentence. This topic is the control plane; the stream is the data plane.

ORDERING KEY IS THE CONVERSATION ID. Two turns published for one conversation
must run in order or the second will read a message history the first has not
finished writing. Ordering keys give per-key FIFO with no global bottleneck, so
unrelated conversations still run fully in parallel. Note that ordering requires
the publisher and subscription to agree — the subscription must be created with
`--enable-message-ordering` or the guarantee is silently lost.

THE PUBLISHER CLIENT IS SYNC AND THREADED. google-cloud-pubsub has no asyncio
publisher; `publish()` returns a concurrent.futures.Future resolved on its own
thread. Awaiting that future directly would block the event loop, so publish()
below hands it to the running loop via asyncio.wrap_future.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

_publisher: Any = None
_topic_path: str | None = None
_unavailable = False


def is_enabled() -> bool:
    """Whether turns can be dispatched to a worker."""
    return bool(settings.PUBSUB_TOPIC) and not _unavailable


def _project_id() -> str:
    if settings.GOOGLE_CLOUD_PROJECT:
        return settings.GOOGLE_CLOUD_PROJECT
    # On GKE the metadata server answers this, so the variable does not have to
    # be set explicitly in the ConfigMap. google.auth caches the lookup.
    import google.auth

    _, project = google.auth.default()
    if not project:
        raise RuntimeError(
            "Could not determine GCP project for Pub/Sub. Set GOOGLE_CLOUD_PROJECT."
        )
    return str(project)


def _get_publisher() -> tuple[Any, str] | None:
    global _publisher, _topic_path, _unavailable

    if not settings.PUBSUB_TOPIC or _unavailable:
        return None
    if _publisher is not None and _topic_path is not None:
        return _publisher, _topic_path

    try:
        from google.cloud import pubsub_v1

        # enable_message_ordering is a publisher-wide setting, not per-message:
        # without it, supplying an ordering_key raises rather than being ignored,
        # which is a good failure but a confusing one if you have not seen it.
        client = pubsub_v1.PublisherClient(
            publisher_options=pubsub_v1.types.PublisherOptions(
                enable_message_ordering=True,
                # Pub/Sub carries its own OTel support rather than having an
                # instrumentation package, and it ships off. On, it injects
                # trace context into the message so the worker's turn continues
                # the API request's trace instead of starting an orphan — which
                # is the whole reason a queued turn is traceable end to end.
                enable_open_telemetry_tracing=True,
            )
        )
        path = client.topic_path(_project_id(), settings.PUBSUB_TOPIC)
    except Exception:
        _unavailable = True
        logger.exception(
            "Pub/Sub publisher unavailable — /chat will run turns inline for the "
            "life of this process (turns will not survive client disconnect)."
        )
        return None

    _publisher, _topic_path = client, path
    logger.info("Pub/Sub publisher ready: %s", path)
    return client, path


async def publish_turn(
    turn_id: str,
    conversation_id: str | None,
    payload: dict,
) -> bool:
    """Dispatch a turn to the worker pool. Returns False if it could not be sent.

    A False return is the caller's signal to fall back to running the turn
    inline, which is worse (it dies with the connection) but far better than
    returning an error to a user whose only problem is that a topic is
    misconfigured.
    """
    got = _get_publisher()
    if got is None:
        return False
    client, path = got

    data = json.dumps(payload, default=str).encode("utf-8")

    # Ordering is per conversation. Guest turns have no conversation, and no
    # ordering requirement either — each is a standalone request — so they key on
    # the turn id, which keeps every message ordered-but-independent rather than
    # funnelling all guests through one FIFO queue.
    ordering_key = conversation_id or turn_id

    try:
        future = client.publish(
            path,
            data,
            ordering_key=ordering_key,
            # Attributes are indexable by subscription filters, so a future
            # subscription can select on them without decoding the body.
            turn_id=turn_id,
            conversation_id=conversation_id or "",
        )
        # wrap_future bridges the publisher's own thread to this event loop.
        # Awaited rather than fired-and-forgotten so a topic that does not exist
        # surfaces here — where the inline fallback can still take over — instead
        # of after the response has been sent.
        message_id = await asyncio.wrap_future(future)
    except Exception:
        logger.exception("Failed to publish turn %s", turn_id)
        # One publish failing does not mean the topic is broken (a transient
        # deadline, a resumed ordering key), so this does NOT latch _unavailable
        # the way a construction failure does. The caller falls back for this
        # turn only, and the next one tries again.
        _resume_ordering(client, path, ordering_key)
        return False

    logger.info(
        "published turn %s (conversation=%s, message_id=%s)",
        turn_id,
        conversation_id,
        message_id,
    )
    return True


def _resume_ordering(client: Any, path: str, ordering_key: str) -> None:
    """Unwedge an ordering key after a publish failure.

    Pub/Sub deliberately fails every subsequent publish on an ordering key once
    one has failed, to avoid delivering out of order. Without this call, a single
    transient error would permanently break dispatch for that conversation while
    every other conversation kept working — an unusually confusing failure.
    """
    try:
        client.resume_publish(path, ordering_key)
    except Exception:
        logger.debug("resume_publish failed for %s", ordering_key, exc_info=True)


def close() -> None:
    """Flush pending publishes at shutdown."""
    global _publisher, _topic_path
    if _publisher is None:
        return
    client, _publisher, _topic_path = _publisher, None, None
    try:
        client.stop()
    except Exception:
        logger.debug("Pub/Sub publisher stop failed", exc_info=True)


def reset_for_tests() -> None:
    global _publisher, _topic_path, _unavailable
    _publisher = None
    _topic_path = None
    _unavailable = False
