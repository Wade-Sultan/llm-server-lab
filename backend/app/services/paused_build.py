"""Saving and reclaiming a build paused at the case step.

THE PROBLEM THIS SOLVES. The pipeline makes nine LLM-backed decisions before it
reaches the case step, then has to stop and ask a human which of three cases
they want. Holding the turn open across that wait costs a blocked worker thread
and a Pub/Sub lease for as long as the user takes to answer — and still fails
whoever answers after the timeout. So the turn ends instead: everything decided
so far is written here, and the pick starts a fresh turn that picks the
pipeline back up exactly where it stopped.

TWO STORES, ONE ANSWER. Valkey is the read path and answers essentially every
resume within its TTL. Postgres is the durable copy, because eviction or expiry
here does not cost a cache miss — it costs the whole build. `load_and_claim`
tries them in that order and each store's claim is atomic on its own terms:
GETDEL on Valkey, a conditional UPDATE on Postgres. Whichever answers first,
the resume happens at most once, so a double-click or a redelivered message
cannot produce two builds.

WHAT IS NOT HERE. A never-resumed pause leaves a row behind with `resumed_at`
still null. Sweeping those — and recording them as ABANDONED build_sessions,
which is what that status was defined for — wants a periodic job rather than a
request path, so `created_at` is indexed for it and nothing here deletes them.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from redis.exceptions import RedisError
from sqlalchemy import select, update

from app.core.db import AsyncSessionLocal
from app.core.valkey import get_client
from app.models.paused_build import PausedBuild

logger = logging.getLogger(__name__)

# A day. Long enough that a user who picks the next morning is served from
# Valkey rather than falling through to Postgres, short enough that abandoned
# builds do not accumulate in memory. Postgres is what makes the ceiling soft:
# past this, the resume still works, it just costs a query.
_TTL_S = 24 * 60 * 60


def _key(token: str) -> str:
    return f"build:paused:{token}"


async def save(token: str, conversation_id: str | None, payload: dict) -> bool:
    """Persist a paused build to both stores.

    Returns True if at least one store accepted it. False means the pause could
    not be recorded anywhere, and the caller must not tell the user to pick a
    case it will not be able to act on.
    """
    import json

    stored = False

    # Postgres first: it is the copy that has to exist for the pause to be
    # honest, and writing it second would leave a window where Valkey promises
    # a resume that nothing durable can satisfy.
    try:
        conv_uuid = _as_uuid(conversation_id)
        async with AsyncSessionLocal() as db:
            db.add(
                PausedBuild(
                    token=token, payload=payload, conversation_id=conv_uuid
                )
            )
            await db.commit()
        stored = True
    except Exception:
        logger.warning("paused build could not be written to Postgres", exc_info=True)

    client = await get_client()
    if client is not None:
        try:
            await client.set(
                _key(token), json.dumps(payload, default=str), ex=_TTL_S
            )
            stored = True
        except (RedisError, TypeError):
            logger.warning("paused build could not be written to Valkey", exc_info=True)

    return stored


async def load_and_claim(token: str, conversation_id: str | None) -> dict | None:
    """The paused build for `token`, claimed so nothing else can resume it.

    `conversation_id` is the conversation the resuming turn will append to, and
    the claim only succeeds if it is the same one that paused the build. That
    is what keeps a pause and its resume on one thread: the token alone says
    *which* build, not *whose* conversation, and a resumed build is written
    wherever the resuming turn says.

    Returns None when the token is unknown, already resumed, aimed at the wrong
    conversation, or lost from both stores — all of which the caller handles
    identically, because from the user's side they are the same event: this
    pick cannot be acted on.
    """
    import json

    client = await get_client()
    if client is not None:
        try:
            # Read before claiming so a pick from the wrong conversation is
            # turned away without consuming the pause — a claim-then-reject
            # would let anyone holding a token burn a build they cannot resume.
            raw = await client.get(_key(token))
            if raw is not None:
                payload = json.loads(raw)
                if not _same_conversation(payload, conversation_id):
                    _log_mismatch(token, payload, conversation_id)
                    return None
                # GETDEL is the claim proper: two picks that both pass the
                # check above still cannot both come away with the payload.
                if await client.getdel(_key(token)) is None:
                    return None
                await _mark_resumed(token)
                return payload
        except (RedisError, ValueError):
            logger.warning(
                "paused build read from Valkey failed; falling through to Postgres",
                exc_info=True,
            )

    return await _claim_in_postgres(token, conversation_id)


async def _claim_in_postgres(token: str, conversation_id: str | None) -> dict | None:
    """Claim and return the durable copy, or None if it is gone or taken.

    Verify first, claim second, for the same reason the Valkey path does: a
    pick aimed at the wrong conversation must be turned away without consuming
    a pause its rightful conversation could still finish. The window between
    the two statements is harmless, because the UPDATE is the claim and
    `resumed_at IS NULL` makes it atomic on its own.

    The check reads the conversation out of the PAYLOAD rather than off the
    column. They agree for real conversations, but `conversation_id` is a UUID
    column and a guest's thread id is the synthetic string "turn:<uuid>",
    which `_as_uuid` stores as NULL — so every guest pause has the same column
    value and matching on it would let any guest pause satisfy any other. The
    payload keeps the id verbatim, which is what makes the check exact.
    """
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(PausedBuild.payload).where(
                    PausedBuild.token == token,
                    PausedBuild.resumed_at.is_(None),
                )
            )
            row = result.first()
            if row is None:
                return None
            payload = row[0]
            if not _same_conversation(payload, conversation_id):
                _log_mismatch(token, payload, conversation_id)
                return None

            claimed = await db.execute(
                update(PausedBuild)
                .where(
                    PausedBuild.token == token,
                    PausedBuild.resumed_at.is_(None),
                )
                .values(resumed_at=datetime.now(UTC))
                .returning(PausedBuild.id)
            )
            won = claimed.first() is not None
            await db.commit()
            return payload if won else None
    except Exception:
        logger.warning("paused build claim in Postgres failed", exc_info=True)
        return None


def _same_conversation(payload: dict, conversation_id: str | None) -> bool:
    """Whether this pick belongs to the conversation that paused the build.

    A resumed build is appended to whatever conversation the resuming turn
    names, so without this a pick could graft a build onto a different thread
    than the one whose picker was clicked — putting the parts, the share link
    and the telemetry somewhere the user never saw them offered.

    Compared as raw strings rather than coerced to UUIDs, because a guest's
    thread id is a synthetic "turn:<uuid>" that is deliberately not one.
    """
    return (payload.get("conversation_id") or None) == (conversation_id or None)


def _log_mismatch(token: str, payload: dict, conversation_id: str | None) -> None:
    logger.warning(
        "case pick for token %s named conversation %r but the paused build "
        "belongs to %r; refusing to resume",
        token,
        conversation_id,
        payload.get("conversation_id"),
    )


async def _mark_resumed(token: str) -> None:
    """Best-effort: stamp the durable row after a Valkey claim already won.

    Not the claim itself — Valkey's GETDEL was — so a failure here costs
    nothing a user can see. It only keeps the table honest for the sweeper.
    """
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(PausedBuild)
                .where(
                    PausedBuild.token == token,
                    PausedBuild.resumed_at.is_(None),
                )
                .values(resumed_at=datetime.now(UTC))
            )
            await db.commit()
    except Exception:
        logger.debug("could not stamp paused build as resumed", exc_info=True)


async def peek(token: str) -> dict | None:
    """The paused build without claiming it. For diagnostics only.

    Deliberately separate from load_and_claim so no caller can accidentally
    read a payload it has not earned the right to act on.
    """
    import json

    client = await get_client()
    if client is not None:
        try:
            raw = await client.get(_key(token))
            if raw is not None:
                return json.loads(raw)
        except (RedisError, ValueError):
            pass
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(PausedBuild.payload).where(PausedBuild.token == token)
            )
            row = result.first()
            return row[0] if row is not None else None
    except Exception:
        return None


def _as_uuid(value: str | None) -> Any:
    import uuid

    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        # Guest turns carry a synthetic "turn:<uuid>" thread id, which is not a
        # conversation and has no row to point at.
        return None
