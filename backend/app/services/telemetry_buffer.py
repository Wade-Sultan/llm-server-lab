"""Write-behind buffer on Valkey for build telemetry.

THE INVARIANT THIS EXISTS TO KEEP. The LangGraph turn may write to Valkey and
read from Postgres, but it must never write to Postgres — persistence belongs to
`save_turn`, which runs after the graph stream has drained. Build telemetry was
the one violation: `BuildRecorder.finish()` opened its own session from inside
the `build` node and committed a `build_sessions` row plus every
`module_decisions` row. This moves that write off the graph path — the recorder
pushes here, and `drain_pending` (called outside the graph) does the insert.

WHY A LIST AND NOT A STREAM. Streams with consumer groups are the textbook
at-least-once primitive, and turn_stream already uses them. They are not needed
here because `build_sessions.id` is a natural idempotency key: the drain inserts
with ON CONFLICT DO NOTHING, so a payload processed twice is a no-op rather than
a duplicate row. That reduces the requirement from "exactly-once delivery" to
"at-least-once delivery plus an idempotent write", which a plain list satisfies.

PEEK, COMMIT, THEN TRIM — never LPOP. Same discipline as chat_buffer's eviction:
entries leave the buffer only once Postgres has confirmed the write. LPOP would
remove them first, and a failure between the pop and the commit would lose
telemetry that had already been paid for in tokens.

NO TTL, A LENGTH CAP INSTEAD. Unlike a chat buffer this key is not per
conversation, so an expiry would silently drop pending work rather than reclaim
an abandoned key. A permanently failing drain is bounded by _MAX_PENDING, which
discards the OLDEST entries — telemetry ages into irrelevance, so under overflow
the recent runs are the ones worth keeping.
"""

from __future__ import annotations

import json
import logging

from redis.exceptions import RedisError

from app.core.valkey import get_client

logger = logging.getLogger(__name__)

PENDING_KEY = "build:telemetry:pending"

# Ceiling on unpersisted payloads. Each carries every candidate set verbatim, so
# these are large (tens of KB); this bounds the key at roughly a few hundred MB
# in the pathological case where the drain has been broken for a long time.
_MAX_PENDING = 5000


async def push(payload: dict) -> bool:
    """Buffer one finished build's telemetry. False if Valkey is unavailable.

    Losing telemetry when Valkey is down is the accepted cost of the invariant:
    the alternative is writing to Postgres from inside the graph, which is the
    thing this module exists to stop. Logged at warning so the loss is visible
    rather than silent.
    """
    client = await get_client()
    if client is None:
        logger.warning(
            "valkey unavailable; dropping build telemetry for session %s",
            payload.get("session_id"),
        )
        return False
    try:
        pipe = client.pipeline()
        pipe.rpush(PENDING_KEY, json.dumps(payload, default=str))
        # Keep the newest _MAX_PENDING. Negative indices count from the tail, so
        # this is "drop everything before the last _MAX_PENDING entries".
        pipe.ltrim(PENDING_KEY, -_MAX_PENDING, -1)
        await pipe.execute()
        return True
    except (RedisError, OSError):
        logger.warning(
            "telemetry buffer push failed for session %s",
            payload.get("session_id"),
            exc_info=True,
        )
        return False


async def peek(limit: int) -> list[dict]:
    """Read up to `limit` pending payloads WITHOUT removing them.

    Undecodable entries come back as None-free: they are skipped here and still
    counted by the caller's `ack`, because a payload that cannot be parsed will
    never persist and leaving it at the head would block the queue forever.
    """
    client = await get_client()
    if client is None or limit <= 0:
        return []
    try:
        raw_entries = await client.lrange(PENDING_KEY, 0, limit - 1)
    except (RedisError, OSError):
        logger.warning("telemetry buffer peek failed", exc_info=True)
        return []

    payloads: list[dict] = []
    for raw in raw_entries:
        try:
            payloads.append(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            logger.warning("undecodable telemetry payload; it will be dropped")
    return payloads


async def ack(count: int) -> bool:
    """Drop the first `count` entries. Call only after Postgres confirms.

    `count` is how many were READ, not how many parsed — see peek. Trimming by
    the read count is what keeps a poison entry from wedging the head of the
    queue behind entries that would persist fine.
    """
    client = await get_client()
    if client is None or count <= 0:
        return False
    try:
        await client.ltrim(PENDING_KEY, count, -1)
        return True
    except (RedisError, OSError):
        # Not retried. The drain is idempotent (ON CONFLICT DO NOTHING), so the
        # worst case of a failed trim is the same rows being re-attempted.
        logger.warning("telemetry buffer ack failed", exc_info=True)
        return False


async def count_pending() -> int | None:
    """Depth of the buffer, or None if Valkey is unavailable.

    Steady state is near zero — entries are drained after each turn. A number
    that climbs means the drain is failing, which is exactly the condition where
    the telemetry feeding GEPA is quietly going missing.
    """
    client = await get_client()
    if client is None:
        return None
    try:
        return int(await client.llen(PENDING_KEY))
    except (RedisError, OSError):
        logger.warning("telemetry buffer depth check failed", exc_info=True)
        return None
