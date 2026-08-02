"""Per-turn event stream on Valkey, using Redis Streams.

WHY A STREAM AND NOT PUB/SUB (either Valkey's or Google's). Both are fire-and-
forget: a subscriber only sees what is published while it is attached. The
browser attaches *after* POST /chat returns, so with pub/sub every event the
worker emitted in between is simply gone — and that gap is exactly the "Building
your PC…" progress the user is waiting to see. A stream is a durable log with
monotonic IDs, so a reader can start at 0 and replay from the beginning, or
resume from the last ID it saw. That is what makes both the initial attach and a
mid-build reconnect work, and it is why the frontend can send Last-Event-ID.

KEY LAYOUT. `chat:evt:{<turn_id>}` — the braces are a real cluster hash tag, not
formatting. Everything for one turn hashes to one slot, so the stream and the
chat buffer (app/services/chat_buffer.py) would live on the same shard and could
be touched together without a cross-slot error.

That is deliberately future-proofing rather than a present requirement: prod runs
a Cluster Mode Disabled instance (forced by the `custom-pico` node type — see
app/core/valkey.py), where there is one shard and slots are irrelevant. The tags
cost nothing there and mean a later move to a clustered instance is a config flip
instead of a key-naming migration. Keep them.

TERMINATION IS EXPLICIT. The last entry is always `{"type": "end"}`, written by
the producer after persistence has run. Readers stop on it. Inferring the end
from the pipeline's own `done` event would race: `done` is emitted before
_save_turn commits, so a reader that stopped there could disconnect while the
turn was still being written, and an evict-on-commit buffer would be deleted
under a reader that had not finished.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

from redis.exceptions import RedisError

from app.core.config import settings
from app.core.valkey import get_client

logger = logging.getLogger(__name__)

# Stream entries carry the whole event as one JSON field. Streams support
# multiple fields per entry, but the event shape is open-ended (progress, token,
# build, usage all differ) and a single opaque payload avoids a schema that has
# to be kept in step with chat_pipeline's yields.
_FIELD = "e"

_END = {"type": "end"}


def stream_key(turn_id: str) -> str:
    return f"chat:evt:{{{turn_id}}}"


async def emit(turn_id: str, event: dict) -> bool:
    """Append one event. Returns False if Valkey is unavailable.

    Never raises: a failed emit must not kill the pipeline that produced the
    event. The turn still completes and still persists — the client just stops
    seeing live updates for it.
    """
    client = await get_client()
    if client is None:
        return False

    key = stream_key(turn_id)
    try:
        pipe = client.pipeline()
        # MAXLEN with approx=True trims on node boundaries rather than walking
        # the log on every write. The cap is a safety net against a pathological
        # turn, not a precise limit, so approximate trimming is the right trade.
        pipe.xadd(
            key,
            {_FIELD: json.dumps(event, default=str)},
            maxlen=settings.TURN_STREAM_MAXLEN,
            approximate=True,
        )
        # Pushed forward on every write, so the TTL measures time since the last
        # event rather than since the turn began. A slow build cannot expire its
        # own stream out from under a client that is still watching it.
        pipe.expire(key, settings.TURN_STREAM_TTL_S)
        await pipe.execute()
        return True
    except (RedisError, OSError):
        logger.warning("turn_stream emit failed for %s", turn_id, exc_info=True)
        return False


async def emit_end(turn_id: str) -> bool:
    """Close the stream. Call exactly once, after persistence has run."""
    return await emit(turn_id, _END)


async def tail(
    turn_id: str,
    last_id: str = "0",
    idle_timeout_ms: int = 15_000,
    max_idle_rounds: int = 40,
) -> AsyncIterator[tuple[str, dict] | None]:
    """Yield `(entry_id, event)` until the terminal entry, or None when idle.

    A None yield means "no events this round" and exists so the caller can write
    an SSE heartbeat; without it an idle build sits silent long enough for the
    Gateway to reap the connection as dead.

    `last_id` is exclusive, matching XREAD semantics, so passing back the last ID
    a client received resumes exactly after it with no duplicate and no gap.
    Default "0" replays the stream from the beginning — correct for a first
    attach, which must not miss events the worker emitted before the browser got
    here.

    Gives up after `max_idle_rounds` consecutive silent rounds (default ~10min).
    Bounded because a worker that was OOM-killed mid-turn will never write the
    terminal entry, and an unbounded tail would pin a connection and a Valkey
    connection from the pool forever waiting for it.
    """
    client = await get_client()
    if client is None:
        return

    key = stream_key(turn_id)
    idle_rounds = 0

    while idle_rounds < max_idle_rounds:
        try:
            resp = await client.xread(
                {key: last_id}, count=200, block=idle_timeout_ms
            )
        except (RedisError, OSError):
            # Ends the SSE response rather than erroring it. The client already
            # knows how to reconnect with Last-Event-ID, and it holds the text
            # rendered so far, so a reconnect resumes instead of restarting.
            logger.warning("turn_stream tail failed for %s", turn_id, exc_info=True)
            return

        if not resp:
            idle_rounds += 1
            yield None
            continue

        idle_rounds = 0
        # resp is [(key, [(entry_id, {field: value}), ...])]. One key was
        # requested, so there is exactly one element.
        for entry_id, fields in resp[0][1]:
            last_id = entry_id
            raw = fields.get(_FIELD)
            if raw is None:
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("undecodable entry %s on %s", entry_id, key)
                continue
            if event.get("type") == "end":
                return
            yield entry_id, event

    logger.info(
        "turn_stream tail for %s gave up after %ss with no terminal entry",
        turn_id,
        (idle_timeout_ms * max_idle_rounds) // 1000,
    )


async def claim(turn_id: str, owner: str, ttl_s: int) -> bool:
    """Take exclusive ownership of a turn. False means someone else has it.

    Pub/Sub is at-least-once, so a worker must expect to be handed a turn that
    another worker is already running — most often because the first worker took
    longer than the ack deadline, not because anything failed. Without this
    guard, redelivery would run the pipeline a second time: the user would watch
    duplicated text interleave into their stream, and the turn would be billed to
    OpenRouter twice.

    SET NX is the whole mechanism. It is atomic on a single key, so it is safe
    across shards and needs no Lua. The TTL bounds a worker that dies holding a
    claim; it must exceed the longest plausible turn or a slow build could be
    picked up concurrently by a redelivery.

    Returns True when Valkey is unavailable — an unclaimed run is strictly better
    than refusing to run the turn at all, and the inline fallback has no
    redelivery to protect against anyway.
    """
    client = await get_client()
    if client is None:
        return True
    try:
        return bool(await client.set(f"chat:claim:{{{turn_id}}}", owner, nx=True, ex=ttl_s))
    except (RedisError, OSError):
        logger.warning("turn_stream claim failed for %s", turn_id, exc_info=True)
        return True


async def release_claim(turn_id: str) -> None:
    """Drop the claim so a failed turn can be retried on redelivery.

    Only called when a turn did NOT complete. A completed turn keeps its claim
    until the TTL expires, which is what makes a late redelivery a no-op instead
    of a re-run.
    """
    client = await get_client()
    if client is None:
        return
    try:
        await client.delete(f"chat:claim:{{{turn_id}}}")
    except (RedisError, OSError):
        logger.warning("turn_stream claim release failed for %s", turn_id, exc_info=True)


async def exists(turn_id: str) -> bool:
    """Whether any events have been written for this turn yet."""
    client = await get_client()
    if client is None:
        return False
    try:
        return bool(await client.exists(stream_key(turn_id)))
    except (RedisError, OSError):
        logger.warning("turn_stream exists check failed for %s", turn_id, exc_info=True)
        return False
