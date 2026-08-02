"""Valkey (Memorystore) connection management.

Valkey speaks the Redis wire protocol, so this is redis-py throughout — there is
no separate client library, and `import redis` here is not a mistake.

CLUSTER MODE IS THE DEFAULT, and getting it wrong is the most likely way to lose
an afternoon: Memorystore for Valkey always speaks the cluster protocol, even at
one shard. A plain `Redis` client connects and issues commands happily until the
first key that hashes outside the node it happened to reach, then fails with a
MOVED error that surfaces as an unrelated-looking exception mid-turn. `RedisCluster`
reads CLUSTER SLOTS at connect time and routes correctly. `VALKEY_CLUSTER=false`
exists only for a standalone valkey container in local dev.

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
    """Whether Valkey is configured at all. Cheap; safe to call per request."""
    return bool(settings.VALKEY_HOST) and not _unavailable


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
        if settings.VALKEY_CLUSTER:
            # XREAD BLOCK parks a connection for the whole block duration. In
            # cluster mode redis-py keeps a per-node pool, and the default
            # max_connections is shared across every tailing request on this pod,
            # so it has to clear the SSE fan-in ceiling with room to spare.
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
