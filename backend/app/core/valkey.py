"""Valkey (Memorystore) connection management.

Valkey speaks the Redis wire protocol, so this is redis-py throughout — there is
no separate client library, and `import redis` here is not a mistake.

CLUSTER MODE MUST MATCH THE INSTANCE, and getting it wrong is the most likely way
to lose an afternoon, because the two mismatches fail in opposite directions:

  Redis client -> clustered instance   Connects fine, then dies on the first key
                                       that hashes outside the node it reached,
                                       with a MOVED error surfacing as an
                                       unrelated-looking exception mid-turn.
  RedisCluster -> non-clustered        Fails at connect: there is no CLUSTER
                                       SLOTS to read.

`VALKEY_CLUSTER` defaults to False, which is correct for both real deployments.
Production runs a `custom-pico` node — the smallest node type Google sells with
an SLA, and one they offer on **Cluster Mode Disabled instances only** — and local
dev runs a standalone valkey container. (`shared-core-nano` is cheaper and does
speak the cluster protocol, but it has no SLA and Google documents it as
development-only.)

Nothing here depends on the non-clustered shape. The key layout in
app/services/turn_stream.py is already cluster-safe, so moving to a Cluster Mode
Enabled instance later is a config flip rather than a rewrite.

DEGRADES RATHER THAN CRASHES. Every accessor returns None when Valkey is
unconfigured or unreachable, and callers treat that as "no buffering, no resume"
instead of an error. Losing the ability to resume a stream should not take the
API down with it.
"""

from __future__ import annotations

import logging
from typing import Any

from redis.asyncio import Redis, RedisCluster
from redis.exceptions import RedisError

from app.core.config import settings

logger = logging.getLogger(__name__)

# Module-level singleton. redis-py maintains its own connection pool internally,
# so one client per process is correct; building a second would double the
# connection count against Memorystore's per-instance limit for no benefit.
_client: Redis | RedisCluster | None = None
_unavailable = False


def is_enabled() -> bool:
    """Whether Valkey is *configured*. A config check, NOT a health check.

    Returns True before the first connection is ever attempted, so it cannot be
    used to decide whether streaming will actually work. Use is_available() for
    that — see the note there for what goes wrong otherwise.
    """
    return bool(settings.VALKEY_HOST) and not _unavailable


async def is_available() -> bool:
    """Whether Valkey is configured AND reachable. Connects on first call.

    THE DISTINCTION FROM is_enabled() IS LOAD-BEARING, and getting it wrong
    produces the least debuggable failure this system has. `/chat` decides
    between dispatching a turn to a worker and running it inline. If that
    decision is made on configuration alone, then a builder pod that cannot
    reach Valkey will still publish to Pub/Sub — the worker picks the turn up,
    runs it, bills OpenRouter and commits it to Postgres, all perfectly — while
    the pod holding the browser connection has no way to read the events back.
    The user gets an empty response, and nothing in either pod's log says why,
    because nothing failed.

    Checking reachability first means that pod falls back to inline streaming
    instead: the turn still works, it just isn't durable across a disconnect.

    Cheap after the first call — get_client() caches the client, and the
    unavailable latch means a broken deployment pays one timeout per process
    rather than one per request.
    """
    return await get_client() is not None


async def get_client() -> Redis | RedisCluster | None:
    """Return the shared client, or None if Valkey is unconfigured/unreachable.

    Connects lazily on first use rather than at import, so that merely importing
    app.main (alembic, the test suite, the reloader parent) never opens a socket.
    """
    global _client, _unavailable

    if not settings.VALKEY_HOST or _unavailable:
        return None
    if _client is not None:
        return _client

    common: dict[str, Any] = {
        "host": settings.VALKEY_HOST,
        "port": settings.VALKEY_PORT,
        # Events are JSON strings and stream IDs are ASCII, so decoding at the
        # client keeps every call site from sprinkling .decode() around.
        "decode_responses": True,
        # Without a timeout a network partition parks a request forever holding
        # a worker slot. 5s is far longer than a healthy in-VPC round trip.
        "socket_timeout": 5.0,
        "socket_connect_timeout": 5.0,
        # Memorystore drops idle connections; without keepalive the first command
        # after a lull fails once before the pool replaces the connection.
        "socket_keepalive": True,
    }
    if settings.VALKEY_PASSWORD:
        common["password"] = settings.VALKEY_PASSWORD
    if settings.VALKEY_TLS:
        # Server-authenticated TLS against Google's CA. Memorystore does not do
        # client certs, so there is nothing to present from this side.
        common["ssl"] = True
        common["ssl_cert_reqs"] = "required"

    try:
        # 128 either way. XREAD BLOCK parks a connection for the whole block
        # duration, so the pool ceiling is really the SSE fan-in ceiling: every
        # browser tailing a turn on this pod holds one. The default (roughly 2^31
        # in redis-py, bounded in practice by the OS) is not the problem — being
        # explicit here is, so that a future reduction is a deliberate act.
        if settings.VALKEY_CLUSTER:
            client: Redis | RedisCluster = RedisCluster(
                max_connections=128, **common
            )
        else:
            client = Redis(max_connections=128, **common)
        await client.ping()
    except (RedisError, OSError):
        # Latch off rather than retrying per call: if Valkey is misconfigured,
        # every request would otherwise pay a 5s connect timeout. A pod restart
        # is the recovery path, and on GKE that is what a rollout does anyway.
        _unavailable = True
        logger.exception(
            "Valkey unreachable at %s:%s — turn streaming and chat buffering are "
            "disabled for the life of this process; /chat falls back to inline "
            "streaming (turns will not survive client disconnect).",
            settings.VALKEY_HOST,
            settings.VALKEY_PORT,
        )
        return None

    _client = client
    logger.info(
        "Valkey connected: %s:%s (cluster=%s, tls=%s)",
        settings.VALKEY_HOST,
        settings.VALKEY_PORT,
        settings.VALKEY_CLUSTER,
        settings.VALKEY_TLS,
    )
    return _client


async def close_client() -> None:
    """Release the pool at shutdown. Idempotent."""
    global _client
    if _client is None:
        return
    client, _client = _client, None
    try:
        await client.aclose()
    except Exception:
        logger.debug("Valkey close failed (shutting down anyway)", exc_info=True)


def reset_for_tests() -> None:
    """Drop the cached client and clear the unavailable latch.

    The latch is process-wide and deliberately sticky, which would otherwise make
    one test that simulates an outage poison every test after it.
    """
    global _client, _unavailable
    _client = None
    _unavailable = False
