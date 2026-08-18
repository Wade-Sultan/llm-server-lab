"""Write-behind chat buffer on Valkey, keyed by conversation.

THE PROBLEM THIS SOLVES. Today a turn is only durable once _save_turn commits at
the very end of the request (app/api/routes/chat.py). Everything before that —
the accumulated assistant text, the resolved build, the OpenRouter spend — lives
only in local variables inside a generator. If the client disconnects, FastAPI
cancels that generator and all of it is discarded, including cost that was
already incurred and should still be billed to the conversation.

So the turn is written here first, incrementally, and deleted only once Postgres
has confirmed the commit. A crash between the two leaves a buffer behind, which
is the recoverable state; a crash before the buffer write loses only a turn that
had produced nothing yet.

EVICTION IS ON CONFIRMED COMMIT, NOT ON COMPLETION. `discard()` is called after
`db.commit()` returns, never after _save_turn merely finishes — _save_turn
swallows its exceptions and returns normally on failure, so "the function
returned" and "the rows are in Postgres" are very different claims. See
`_save_turn`'s `committed` flag.

CHEAP TTL AS BACKSTOP. CHAT_BUFFER_TTL_S expires anything that never commits, so
a worker killed mid-turn cannot leak a key forever. It is deliberately longer
than the stream's TTL so the buffer outlives the events describing it.
"""

from __future__ import annotations

import asyncio
import json
import logging

from redis.exceptions import RedisError

from app.core.config import settings
from app.core.turn_metrics import CHAT_BUFFERS_RETAINED
from app.core.valkey import get_client

logger = logging.getLogger(__name__)


def buffer_key(conversation_id: str) -> str:
    # Same hash tag as the turn stream, so a conversation's buffer and its event
    # stream land on one shard. See app/services/turn_stream.py.
    return f"chat:buf:{{{conversation_id}}}"


async def save(conversation_id: str, payload: dict) -> bool:
    """Write (or overwrite) the buffered turn. Returns False if unavailable.

    Whole-value overwrite rather than a field-by-field update: a turn is only
    ever written by the one worker running it, so there is no concurrent writer
    to merge against, and a single SET cannot leave a half-updated turn behind
    the way a multi-field HSET sequence interrupted mid-way could.
    """
    client = await get_client()
    if client is None:
        return False
    try:
        await client.set(
            buffer_key(conversation_id),
            json.dumps(payload, default=str),
            ex=settings.CHAT_BUFFER_TTL_S,
        )
        return True
    except (RedisError, OSError):
        logger.warning("chat_buffer save failed for %s", conversation_id, exc_info=True)
        return False


async def load(conversation_id: str) -> dict | None:
    """Read the buffered turn, or None if there isn't one."""
    client = await get_client()
    if client is None:
        return None
    try:
        raw = await client.get(buffer_key(conversation_id))
    except (RedisError, OSError):
        logger.warning("chat_buffer load failed for %s", conversation_id, exc_info=True)
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # A corrupt buffer is worse than none: it would be replayed into
        # Postgres on recovery. Drop it and let the TTL path handle the rest.
        logger.warning("chat_buffer for %s is undecodable; dropping", conversation_id)
        await discard(conversation_id)
        return None


async def count_retained() -> int | None:
    """Count buffers still waiting to be persisted. None if Valkey is unavailable.

    Backs the `palladium_chat_buffers_retained` gauge. Steady state is 0 — a
    buffer only outlives its turn when the Postgres commit failed — so any
    non-zero reading is a set of turns the user already paid for that are not in
    the database.

    SCAN, never KEYS. KEYS is O(N) *and* blocks the server for the whole
    traversal, which on a shared instance means blocking every in-flight turn's
    XREAD too. SCAN is cursor-based and interruptible. In cluster mode redis-py
    fans `scan_iter` out across every primary, so this is one pass per shard.

    Cheap here only because the key count is small by construction: buffers are
    deleted on commit and TTL'd otherwise. If that ever stops being true, this
    scan is itself the thing that gets expensive — which, conveniently, is also
    exactly when the gauge it feeds is screaming.
    """
    client = await get_client()
    if client is None:
        return None
    try:
        count = 0
        async for _ in client.scan_iter(match="chat:buf:*", count=500):
            count += 1
        return count
    except (RedisError, OSError):
        logger.warning("chat_buffer retained-count scan failed", exc_info=True)
        return None


async def discard(conversation_id: str) -> bool:
    """Evict the buffer. Call only after Postgres has confirmed the commit.

    Returns True if a key was actually removed, which lets callers distinguish
    "evicted" from "there was nothing to evict" — the latter is normal for guest
    turns, which are never persisted and so are never buffered.
    """
    client = await get_client()
    if client is None:
        return False
    try:
        return bool(await client.delete(buffer_key(conversation_id)))
    except (RedisError, OSError):
        # Not fatal, and deliberately not retried: the TTL is the backstop, and
        # a stale buffer is only ever read by an explicit recovery path.
        logger.warning(
            "chat_buffer eviction failed for %s; TTL will reclaim it in %ss",
            conversation_id,
            settings.CHAT_BUFFER_TTL_S,
            exc_info=True,
        )
        return False


async def buffer_gauge_loop(interval_s: int = 60) -> None:
    """Keep `palladium_chat_buffers_retained` current.

    Sampled on a loop rather than updated at the point of retention, because the
    question it answers is "how many turns are unpersisted right now" — including
    ones retained by a worker that has since been replaced, which no in-process
    counter would know about.

    Every replica reports the same instance-wide number under its own pod label,
    so the alert in deploy/monitoring/ reduces with REDUCE_MAX rather than
    REDUCE_SUM; summing would multiply the count by the replica count.

    RUN BY BOTH THE API AND THE WORKER, which is why it lives here rather than in
    worker.py where it started. The alert this feeds
    (deploy/monitoring/alert-worker-metrics-absent.yaml) is an ABSENCE
    condition: it fires when nothing has reported for 10 minutes. Once the
    worker scales to zero between bursts — keda-worker.yaml, minReplicaCount 0 —
    a worker-only gauge goes silent on every quiet stretch and pages for an idle
    cluster. builder never scales below 2 (hpa.yaml), so hosting the same loop
    there keeps the series alive continuously and the alert keeps meaning what
    its header says it means.

    The two things it actually detects both survive the move intact: Valkey
    being unreachable (count_retained returns None from any pod, so the gauge
    stops being set) and buffers accumulating (an instance-wide SCAN, not a
    per-pod count — a builder replica sees turns a worker retained just as well
    as another worker would).
    """
    while True:
        try:
            count = await count_retained()
            if count is not None:
                CHAT_BUFFERS_RETAINED.set(count)
                if count:
                    logger.warning(
                        "%d chat buffer(s) awaiting persistence — these are turns "
                        "that did not reach Postgres (deploy/messaging.md §5)",
                        count,
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never let a monitoring loop take down the process it monitors.
            logger.exception("buffer gauge sample failed")
        await asyncio.sleep(interval_s)
