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
from app.core.valkey import (
    BLOCKING_READ_TIMEOUT_S,
    get_blocking_client,
    get_client,
)

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

    USES THE BLOCKING CLIENT, which is the whole reason that client exists. The
    ordinary pool's 5s socket timeout is shorter than the block below, and
    redis-py applies the socket timeout to a parked read like any other: the
    XREAD is aborted at 5s, retried, and finally raised as a TimeoutError. The
    caller sees that as "no worker wrote to this stream" — which is how a cold
    worker pool turned into "That build didn't start. Please try again." on
    every /chat. See app/core/valkey.py's BLOCKING_READ_TIMEOUT_S.
    """
    if idle_timeout_ms >= BLOCKING_READ_TIMEOUT_S * 1000:
        # Loud rather than silent: the failure this prevents looks like an empty
        # stream, not like a misconfiguration, and it costs a whole turn.
        raise ValueError(
            f"idle_timeout_ms={idle_timeout_ms} must stay under the blocking "
            f"client's socket timeout ({BLOCKING_READ_TIMEOUT_S}s), or every "
            "read aborts before the block elapses."
        )

    client = await get_blocking_client()
    if client is None:
        return

    key = stream_key(turn_id)
    idle_rounds = 0

    while idle_rounds < max_idle_rounds:
        try:
            resp = await client.xread({key: last_id}, count=200, block=idle_timeout_ms)
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


def active_turn_key(conversation_id: str) -> str:
    # Same hash tag as the stream and the buffer, so a conversation's pointer
    # and the stream it points at land on one shard.
    return f"chat:active:{{{conversation_id}}}"


async def set_active_turn(conversation_id: str, turn_id: str) -> bool:
    """Record which turn is currently running for a conversation.

    THIS IS WHAT MAKES RESUME POSSIBLE. assistant-transport's resume request
    carries the thread id and its own last state, but no run id — so the server
    is the only party that can say which turn a reconnecting browser should be
    reattached to. Without this pointer a reload mid-build has no way back to a
    turn that is still running, which is the case the whole dispatch
    architecture exists to serve.

    Last writer wins, deliberately. Turns for one conversation are serialised by
    the Pub/Sub ordering key, so a second turn starting means the first is over.

    TTL matches the stream's: a pointer that outlived the stream it names would
    send a reconnecting client to a 404 instead of to a fresh turn.
    """
    client = await get_client()
    if client is None:
        return False
    try:
        await client.set(
            active_turn_key(conversation_id),
            turn_id,
            ex=settings.TURN_STREAM_TTL_S,
        )
        return True
    except (RedisError, OSError):
        logger.warning(
            "could not record active turn for conversation %s; a reconnect "
            "mid-build will not find it",
            conversation_id,
            exc_info=True,
        )
        return False


async def get_active_turn(conversation_id: str) -> str | None:
    """The turn currently running for a conversation, if any.

    A returned id is not a promise the turn is still running — it may have
    finished, in which case its stream replays to completion and terminates
    immediately, which is exactly what a reconnecting client wants anyway.
    """
    client = await get_client()
    if client is None:
        return None
    try:
        return await client.get(active_turn_key(conversation_id))
    except (RedisError, OSError):
        logger.warning(
            "active-turn lookup failed for %s", conversation_id, exc_info=True
        )
        return None


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
        return bool(
            await client.set(f"chat:claim:{{{turn_id}}}", owner, nx=True, ex=ttl_s)
        )
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
        logger.warning(
            "turn_stream claim release failed for %s", turn_id, exc_info=True
        )


# ------------------------------------------------------------ wake queue --
#
# A list of turn ids that have been published to Pub/Sub but not yet picked up
# by a worker. It exists for exactly one reader — KEDA's `redis` scaler, which
# polls LLEN to decide whether the worker pool needs to exist at all
# (deploy/overlays/prod/keda-worker.yaml).
#
# WHY NOT JUST USE THE PUB/SUB BACKLOG, which is already the scaler's steady-
# state signal. Because of when it becomes visible, not what it says: Cloud
# Monitoring samples subscription/num_undelivered_messages every 60s and then
# withholds it for up to another 120s, so the metric that proves a turn is
# waiting arrives one to three minutes after the user pressed send. With the
# pool floored at zero that delay IS the cold start. This list is written
# synchronously on the dispatch path and read directly, so the same fact is
# available to the scaler within one polling interval.
#
# It does not replace the Pub/Sub trigger, and must not: Pub/Sub is the real
# queue and the authority on depth under load. This is a wake-up signal that
# happens to use the same per-replica arithmetic, so the two triggers agree
# about how many pods the work deserves and differ only in how fast they say so.
#
# REMOVED AT PICKUP, NOT AT COMPLETION. The question this answers is "does a
# worker exist to take this turn", which is settled the moment one has it — so a
# worker that crashes mid-turn leaks nothing here, and Pub/Sub redelivery (which
# never goes back through /chat) needs no second push. The only way an entry
# outlives its turn is a turn that no worker ever receives, which is a real
# condition the pool should stay up for.
#
# No hash tag on the key. Every other key in this module carries one so a turn's
# state co-locates; this is a single global list, so there is nothing to
# co-locate it with and a tag would only pin it to an arbitrary slot.
WAKE_KEY = "chat:wake"


async def push_wake(turn_id: str) -> bool:
    """Record that a dispatched turn is waiting for a worker.

    Never raises, and a False return is not worth failing the request over: the
    turn is already on Pub/Sub, so the worst case is that the pool wakes on the
    slower Cloud Monitoring trigger instead — which is exactly the behaviour
    this system had before the fast trigger existed.
    """
    client = await get_client()
    if client is None:
        return False
    try:
        pipe = client.pipeline()
        pipe.rpush(WAKE_KEY, turn_id)
        # A backstop against the one leak this design allows. If entries stop
        # being drained — a broken subscription, a dead-lettered turn — the list
        # would otherwise hold one pod up forever, which is precisely the cost
        # scale-to-zero exists to avoid. Refreshed on every push, so it only
        # expires after a genuine lull, by which point an undrained entry is
        # stale by definition.
        pipe.expire(WAKE_KEY, settings.TURN_STREAM_TTL_S)
        await pipe.execute()
        return True
    except (RedisError, OSError):
        logger.warning("wake queue push failed for %s", turn_id, exc_info=True)
        return False


async def clear_wake(turn_id: str) -> None:
    """Drop a turn from the wake queue: a worker has it.

    LREM with count=0 removes every matching element, so a redelivery that
    somehow pushed twice cannot leave a straggler behind.
    """
    client = await get_client()
    if client is None:
        return
    try:
        await client.lrem(WAKE_KEY, 0, turn_id)
    except (RedisError, OSError):
        # Deliberately quiet about the consequence: a failed removal keeps the
        # pool awake slightly longer than necessary, which costs a pod-minute
        # and breaks nothing.
        logger.warning("wake queue clear failed for %s", turn_id, exc_info=True)


async def wake_depth() -> int | None:
    """Turns waiting for a worker, or None if Valkey is unavailable.

    Not used by the application — this is what KEDA reads with LLEN, exposed
    here so the number is inspectable from the API rather than only from a
    redis-cli against a private VPC address.
    """
    client = await get_client()
    if client is None:
        return None
    try:
        return int(await client.llen(WAKE_KEY))
    except (RedisError, OSError):
        logger.warning("wake queue depth check failed", exc_info=True)
        return None


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
