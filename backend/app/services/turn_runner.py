"""Runs one chat turn to completion, independently of who asked for it.

This is the single implementation shared by the worker (app/worker.py, driven by
Pub/Sub) and by the API's inline fallback (app/api/routes/chat.py, used when
Pub/Sub or Valkey is unconfigured). Keeping one implementation matters more than
usual here: the fallback path is the one that runs in local development and in
the test suite, so if it diverged from the worker path, every test would be
exercising code that never runs in production.

The turn's events are written to Valkey (app/services/turn_stream.py) rather than
returned, because the caller holding the HTTP connection is generally not the
process running the turn.

ORDER OF OPERATIONS AT THE END OF A TURN, and each step depends on the one
before it:
  1. pipeline finishes            -> all events emitted
  2. buffer written               -> turn is recoverable if 3 fails
  3. Postgres commit confirmed    -> turn is durable
  4. buffer evicted               -> only now, and only on a confirmed commit
  5. terminal entry written       -> readers may disconnect
Step 5 is last so that a client watching the stream cannot leave before its turn
is durable, which is what makes "the build finished" mean the same thing to the
user and to the database.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from decimal import Decimal

from sqlalchemy import func, select

from app.core.turn_metrics import (
    TURN_COMMITS,
    TURN_DURATION,
    TURNS_INFLIGHT,
)
from app.schemas.chat import ChatMessage
from app.services import chat_buffer, turn_stream
from app.services.chat_pipeline import run_chat_turn

logger = logging.getLogger(__name__)

# Events the pipeline emits for the server's benefit, never the client's:
# `usage` carries OpenRouter spend and `reference_estimate` is a caching signal.
# Forwarding either would leak cost data to the browser.
_INTERNAL_EVENTS = frozenset({"usage", "reference_estimate"})


def save_turn(
    firebase_uid: str,
    firebase_email: str | None,
    conversation_id: str,
    messages: list[ChatMessage],
    assistant_text: str,
    turn_usage: dict | None = None,
    reached_recommendation: bool = False,
    build_data: dict | None = None,
    build_key: str | None = None,
    ref_estimate_data: dict | None = None,
    ref_estimate_key: str | None = None,
) -> bool:
    """Persist this chat turn. Runs in a thread executor (sync SQLAlchemy).

    Returns True only if the commit actually succeeded. That return value is the
    whole point of the signature change from the original: this function swallows
    its exceptions so a save failure cannot break a stream, which means "it
    returned" carries no information about whether anything was written. The
    caller evicts the Valkey buffer on True and only on True — evicting on mere
    completion would throw away the sole remaining copy of a turn that failed to
    persist.
    """
    from app.core.db import SessionLocal
    from app.models.conversation import Conversation
    from app.models.message import Message
    from app.models.reference_build import ReferenceBuild
    from app.models.user import User

    db = SessionLocal()
    try:
        # Get or create the DB user record for this Firebase user
        user = db.execute(
            select(User).where(User.firebase_uid == firebase_uid)
        ).scalar_one_or_none()

        if not user and firebase_email:
            # Try to match by email (user may have registered via the DB signup flow)
            user = db.execute(
                select(User).where(User.email == firebase_email)
            ).scalar_one_or_none()
            if user:
                user.firebase_uid = firebase_uid
                db.add(user)
                db.flush()

        if not user and firebase_email:
            # Auto-provision a record for Firebase-only users
            user = User(
                email=firebase_email,
                firebase_uid=firebase_uid,
                hashed_password="!firebase_oauth",
            )
            db.add(user)
            db.flush()

        if not user:
            logger.warning(
                "Could not resolve DB user for firebase_uid=%s; skipping save",
                firebase_uid,
            )
            # Not a failure to persist so much as nothing to persist, but the
            # buffer must still be kept: it is the only record that this turn
            # happened at all, and it is what a later backfill would read.
            return False

        # Get or create the Conversation row
        conv_uuid = uuid.UUID(conversation_id)
        conversation = db.get(Conversation, conv_uuid)
        if not conversation:
            title = messages[0].content[:100] if messages else "New Build"
            conversation = Conversation(
                id=conv_uuid,
                user_id=user.id,
                title=title,
            )
            db.add(conversation)
            db.flush()

        # Count already-saved messages so we only append new ones. This is also
        # what makes a Pub/Sub redelivery safe: a turn that committed and was
        # then redelivered re-counts the same rows and appends nothing.
        saved_count = db.execute(
            select(func.count(Message.id)).where(Message.conversation_id == conv_uuid)
        ).scalar_one()

        for msg in messages[saved_count:]:
            db.add(Message(
                conversation_id=conv_uuid,
                role=msg.role,
                content=msg.content,
            ))

        if assistant_text:
            db.add(Message(
                conversation_id=conv_uuid,
                role="assistant",
                content=assistant_text,
                metadata_={"build": build_data} if build_data else None,
            ))

        # Roll this turn's OpenRouter spend into the conversation's running total.
        if turn_usage:
            conversation.total_cost_usd = (conversation.total_cost_usd or Decimal(0)) + Decimal(
                str(turn_usage.get("cost_usd") or 0)
            )
            conversation.total_tokens_in = (conversation.total_tokens_in or 0) + int(
                turn_usage.get("tokens_in") or 0
            )
            conversation.total_tokens_out = (conversation.total_tokens_out or 0) + int(
                turn_usage.get("tokens_out") or 0
            )
            conversation.llm_call_count = (conversation.llm_call_count or 0) + int(
                turn_usage.get("llm_call_count") or 0
            )
            new_models = turn_usage.get("models") or []
            if new_models:
                conversation.models_used = sorted(
                    set(conversation.models_used or []) | set(new_models)
                )
            db.add(conversation)

        # Sticky flag: once a conversation has produced a recommendation, it
        # counts as a "completed build" for cost-per-build analytics.
        if reached_recommendation and not conversation.reached_recommendation:
            conversation.reached_recommendation = True
            db.add(conversation)

        # Cache the reference build resolved for this conversation (the
        # budget-still-unknown estimate, or the one resolved alongside a
        # completed turn) so it's never re-resolved — the guaranteed,
        # free-to-fetch fallback for the rest of the conversation.
        if ref_estimate_key and not conversation.reference_build_key:
            conversation.reference_build_key = ref_estimate_key
            conversation.reference_build = ref_estimate_data
            db.add(conversation)

        # Link the conversation to the concrete PCBuild row backing this
        # reference build template, so pc_builds reflects what was actually
        # recommended (not just the abstract build_key).
        if build_key and not conversation.build_id:
            ref_build = db.execute(
                select(ReferenceBuild).where(ReferenceBuild.build_key == build_key)
            ).scalar_one_or_none()
            if ref_build and ref_build.pc_build_id:
                conversation.build_id = ref_build.pc_build_id
                db.add(conversation)

        db.commit()
        return True
    except Exception:
        logger.exception("Failed to save conversation turn")
        db.rollback()
        return False
    finally:
        db.close()


async def run_turn(
    turn_id: str,
    messages: list[ChatMessage],
    user: dict | None,
    conversation_id: str | None,
) -> None:
    """Run one turn end to end, emitting into the turn's Valkey stream.

    Never raises. A turn that fails still gets an apology event and a terminal
    entry, because a reader blocked on a stream that never terminates is a hung
    browser tab, which is a worse failure than a visible error.
    """
    assistant_text = ""
    turn_usage: dict | None = None
    reached_recommendation = False
    build_data: dict | None = None
    build_key: str | None = None
    ref_estimate_data: dict | None = None
    ref_estimate_key: str | None = None

    started = time.monotonic()
    # Incremented before the pipeline and decremented in the outermost finally,
    # so a turn killed by SIGTERM still leaves the gauge correct. A leaked
    # increment here would look exactly like a saturated worker.
    TURNS_INFLIGHT.inc()
    try:
        await _run_turn(
            turn_id, messages, user, conversation_id,
            assistant_text, turn_usage, reached_recommendation,
            build_data, build_key, ref_estimate_data, ref_estimate_key,
        )
    finally:
        TURNS_INFLIGHT.dec()
        TURN_DURATION.observe(time.monotonic() - started)


async def _run_turn(
    turn_id: str,
    messages: list[ChatMessage],
    user: dict | None,
    conversation_id: str | None,
    assistant_text: str,
    turn_usage: dict | None,
    reached_recommendation: bool,
    build_data: dict | None,
    build_key: str | None,
    ref_estimate_data: dict | None,
    ref_estimate_key: str | None,
) -> None:
    """The body of run_turn. Split out only so the metrics wrapper above stays
    readable; there is no second caller."""
    try:
        async for event in run_chat_turn(messages, conversation_id=conversation_id):
            etype = event.get("type")
            if etype == "token":
                assistant_text += event.get("text", "")
            elif etype == "build":
                # The recommend path emitted a build — this conversation is a completed build.
                reached_recommendation = True
                build_data = event.get("data")
                build_key = event.get("key")
            elif etype == "reference_estimate":
                ref_estimate_data = event.get("data")
                ref_estimate_key = event.get("key")
            elif etype == "usage":
                turn_usage = event

            if etype not in _INTERNAL_EVENTS:
                await turn_stream.emit(turn_id, event)
    except asyncio.CancelledError:
        # Worker shutting down (SIGTERM) or the inline request was abandoned.
        # Deliberately no terminal entry: the Pub/Sub message goes un-acked and
        # is redelivered, and a reader that reconnects should keep waiting for
        # the retry rather than being told the turn ended.
        logger.info("turn %s cancelled; leaving stream open for redelivery", turn_id)
        raise
    except Exception:
        logger.exception("Chat pipeline error on turn %s", turn_id)
        await turn_stream.emit(turn_id, {
            "type": "token",
            "text": "\n\nSomething went wrong generating your recommendation. Please try again.",
        })

    # Buffer before persisting, so a crash in the commit leaves the turn
    # recoverable rather than lost.
    persistable = bool(user and conversation_id)
    if not persistable:
        # Guest turns are streamed but never persisted. Counted separately so
        # "failed" stays a real signal rather than being diluted by traffic that
        # was never meant to reach Postgres.
        TURN_COMMITS.labels(result="skipped").inc()
    if persistable:
        assert conversation_id is not None
        await chat_buffer.save(conversation_id, {
            "turn_id": turn_id,
            "conversation_id": conversation_id,
            "firebase_uid": user.get("uid", "") if user else "",
            "firebase_email": user.get("email") if user else None,
            "messages": [m.model_dump() for m in messages],
            "assistant_text": assistant_text,
            "turn_usage": turn_usage,
            "reached_recommendation": reached_recommendation,
            "build_data": build_data,
            "build_key": build_key,
            "ref_estimate_data": ref_estimate_data,
            "ref_estimate_key": ref_estimate_key,
        })

        committed = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: save_turn(
                user.get("uid", "") if user else "",
                user.get("email") if user else None,
                conversation_id,  # type: ignore[arg-type]
                messages,
                assistant_text,
                turn_usage,
                reached_recommendation,
                build_data,
                build_key,
                ref_estimate_data,
                ref_estimate_key,
            ),
        )

        TURN_COMMITS.labels(result="committed" if committed else "failed").inc()

        if committed:
            await chat_buffer.discard(conversation_id)
        else:
            # Left deliberately. CHAT_BUFFER_TTL_S bounds it, and until then it
            # is the only copy of a turn the user already paid for.
            logger.warning(
                "turn %s did not commit; buffer retained for conversation %s",
                turn_id,
                conversation_id,
            )

    await turn_stream.emit_end(turn_id)
