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
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.core.turn_metrics import (
    TURN_COMMITS,
    TURN_DURATION,
    TURNS_INFLIGHT,
)
from app.schemas.chat import ChatMessage
from app.services import chat_buffer, turn_stream
from app.services.chat_pipeline import resume_chat_turn, run_chat_turn

logger = logging.getLogger(__name__)

# Events the pipeline emits for the server's benefit, never the client's:
# `usage` carries OpenRouter spend, `reference_estimate` is a caching signal, and
# `checkpoint` is the graph state mirrored into Postgres. Forwarding the first
# would leak cost data to the browser and the last is meaningless to it.
_INTERNAL_EVENTS = frozenset({"usage", "reference_estimate", "checkpoint"})


def messages_to_write(
    stored: list[tuple[str, str]], incoming: list[tuple[str, str]]
) -> list[tuple[str, str]]:
    """Which of `incoming` is not yet in `stored`, in order.

    Both lists are (role, content). The contract is the one the reported bug
    violated: every message the user sent or the assistant returned ends up
    recorded, exactly once.

    Reconciling by content rather than by count is the whole point. The previous
    implementation counted stored rows and sliced `incoming` at that number,
    which silently wrote nothing at all once the two drifted out of step — and
    they did drift, because the transport was dropping user turns from the state
    it round-tripped.

    Matching the tail past the common prefix, rather than just appending it,
    keeps conversations that the old path already scrambled from gaining
    duplicates: their rows are out of order but they are still there, and this
    fills in what is missing around them.
    """
    return _reconcile(stored, incoming)[0]


def messages_to_drop(
    stored: list[tuple[str, str]], incoming: list[tuple[str, str]]
) -> list[tuple[str, str]]:
    """Which of `stored` the incoming conversation no longer contains.

    The other half of the same reconciliation, and it exists for editing: an
    edit replaces a message and everything that followed from it, so those rows
    have to leave the database or the conversation reloads with the branch the
    user just discarded sitting under the one that replaced it.

    ONLY EVER CALL THIS WHEN THE TURN REALLY IS AN EDIT — `save_turn` gates it on
    `rewound`, which originates at the one place that knows, the `parentId` check
    in api/routes/chat.py. Every other kind of turn can legitimately present a
    shorter or reordered history (a cancelled turn, a redelivery, one of the
    conversations the old count-based bug scrambled), and deleting on that
    evidence would destroy messages to fix a display problem.

    Note what the shared matching loop already protects: a stored row that
    appears anywhere in the incoming tail is consumed as a match, not returned
    here. So a merely reordered conversation drops nothing.
    """
    return _reconcile(stored, incoming)[1]


def _reconcile(
    stored: list[tuple[str, str]], incoming: list[tuple[str, str]]
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """(what `incoming` adds, what `stored` has that `incoming` dropped)."""
    prefix = 0
    while (
        prefix < len(stored)
        and prefix < len(incoming)
        and stored[prefix] == incoming[prefix]
    ):
        prefix += 1

    unmatched = stored[prefix:]
    out: list[tuple[str, str]] = []
    for entry in incoming[prefix:]:
        if entry in unmatched:
            unmatched.remove(entry)
            continue
        out.append(entry)
    return out, unmatched


def _is_open_picker(case_options: dict | None) -> bool:
    """Whether this picker is still asking, rather than reporting a choice."""
    return bool(case_options) and not case_options.get("chosen")


def _apply_case_pick(db: Any, conv_uuid: uuid.UUID, case_options: dict) -> None:
    """Write the resolved picker back onto the message that showed it.

    Matched on the token rather than on position, because a conversation can
    hold several builds and therefore several pickers, and the one being
    resolved is not necessarily the most recent. Best-effort: a picker that
    cannot be located leaves history showing an open card, which is cosmetic
    next to failing the turn that carries the build.
    """
    from app.models.message import Message

    token = case_options.get("token")
    if not token:
        return
    rows = (
        db.execute(select(Message).where(Message.conversation_id == conv_uuid))
        .scalars()
        .all()
    )
    for row in rows:
        existing = (row.metadata_ or {}).get("case_options")
        if not existing or existing.get("token") != token:
            continue
        # Reassigned rather than mutated in place: JSONB columns are tracked by
        # identity, so mutating the dict leaves SQLAlchemy unaware it changed.
        row.metadata_ = {**(row.metadata_ or {}), "case_options": case_options}
        db.add(row)
        return


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
    graph_checkpoint: dict | None = None,
    graph_checkpoint_id: str | None = None,
    rewound: bool = False,
    case_options_data: dict | None = None,
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

        # WHAT THIS USED TO DO, AND WHY IT LOST MESSAGES. It counted the rows
        # already stored and then sliced the incoming list at that number —
        # a row count used as a list index. That is only correct while the
        # incoming list is exactly the stored sequence, and it silently was not:
        # the transport dropped user turns from the state it round-tripped, so
        # the count ran ahead of the list, `messages[saved_count:]` came back
        # empty, and every user message after the first was never written. No
        # error, no log line — just a conversation missing half of itself.
        #
        # Reconciling against the stored rows themselves has no such failure
        # mode. Whatever is in the incoming conversation but not in the database
        # gets written, which is the property actually wanted: every message the
        # user sent or the assistant returned ends up recorded.
        existing = (
            db.execute(
                select(Message)
                .where(Message.conversation_id == conv_uuid)
                .order_by(Message.created_at, Message.id)
            )
            .scalars()
            .all()
        )

        # This turn's reply is reconciled alongside the history rather than
        # appended unconditionally, which is what keeps a Pub/Sub redelivery
        # idempotent: the second delivery matches the stored rows end to end and
        # writes nothing.
        incoming = [(m.role, m.content or "") for m in messages]
        if assistant_text:
            incoming.append(("assistant", assistant_text))

        stored = [(m.role, m.content or "") for m in existing]
        to_write, to_drop = _reconcile(stored, incoming)

        # An edit does not add to the conversation, it rewrites it — so the rows
        # it replaced have to go, or /conversations/{id} rehydrates the discarded
        # branch and the edit appears to have been undone by a page reload.
        #
        # Gated on `rewound` rather than inferred from `to_drop` being non-empty,
        # because plenty of ordinary turns present a history that does not match
        # the stored rows one for one, and none of them mean "delete". See
        # messages_to_drop.
        if rewound and to_drop:
            remaining = list(to_drop)
            for row in existing:
                entry = (row.role, row.content or "")
                if entry in remaining:
                    remaining.remove(entry)
                    db.delete(row)
            logger.info(
                "edit rewound conversation %s: dropped %d message(s)",
                conversation_id,
                len(to_drop) - len(remaining),
            )
            db.flush()

        # Explicit, strictly increasing timestamps. `created_at` defaulted to
        # server_default=func.now(), which in Postgres is the *transaction*
        # timestamp — identical for every row of a turn — and the relationship
        # orders by that column, so a turn's own messages came back in whatever
        # order the planner felt like. The reply could sort above the question
        # that prompted it.
        written_at = datetime.now(UTC)
        for offset, (role, content) in enumerate(to_write):
            is_this_turns_reply = (
                assistant_text
                and role == "assistant"
                and content == assistant_text
                and offset == len(to_write) - 1
            )
            # An unresolved picker (chosen still null) belongs to the turn that
            # showed it. A resolved one is an update to whichever earlier
            # message showed it, applied separately below — attaching it here
            # would put a second picker under the finished build.
            metadata: dict | None = None
            opening_picker = case_options_data if _is_open_picker(case_options_data) else None
            if is_this_turns_reply and (build_data or opening_picker):
                metadata = {}
                if build_data:
                    metadata["build"] = build_data
                if opening_picker:
                    metadata["case_options"] = opening_picker
            db.add(
                Message(
                    conversation_id=conv_uuid,
                    role=role,
                    content=content,
                    metadata_=metadata,
                    created_at=written_at + timedelta(microseconds=offset),
                )
            )

        # A resolved picker updates the message that showed it, which is a turn
        # or more back. Without this a reload after picking would rebuild an
        # open picker above a finished build, inviting a second click that the
        # paused build's one-shot claim would then refuse.
        if case_options_data and not _is_open_picker(case_options_data):
            _apply_case_pick(db, conv_uuid, case_options_data)

        # Roll this turn's OpenRouter spend into the conversation's running total.
        if turn_usage:
            conversation.total_cost_usd = (
                conversation.total_cost_usd or Decimal(0)
            ) + Decimal(str(turn_usage.get("cost_usd") or 0))
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

        # Mirror the graph's final checkpoint. Overwritten every turn rather
        # than appended: only the latest matters here, because Valkey holds the
        # history and this column exists for the case where Valkey no longer
        # does. Written inside this transaction on purpose — a checkpoint that
        # committed while its messages did not would describe a conversation
        # that, as far as Postgres is concerned, never had that turn.
        if graph_checkpoint is not None:
            conversation.graph_checkpoint = graph_checkpoint
            conversation.graph_checkpoint_id = graph_checkpoint_id
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
    rewound: bool = False,
    case_pick: tuple[str, str] | None = None,
) -> None:
    """Run one turn end to end, emitting into the turn's Valkey stream.

    `case_pick` is (token, case_name) when this turn exists to finish a build
    that paused at the case step. Such a turn adds no user message — the pick
    was a click on a card, not something said — and resumes the saved pipeline
    instead of running the graph. Everything after the events are produced
    (buffering, persistence, the terminal entry) is identical, which is why it
    shares this function rather than getting its own.

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
            turn_id,
            messages,
            user,
            conversation_id,
            assistant_text,
            turn_usage,
            reached_recommendation,
            build_data,
            build_key,
            ref_estimate_data,
            ref_estimate_key,
            rewound,
            case_pick,
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
    rewound: bool = False,
    case_pick: tuple[str, str] | None = None,
) -> None:
    """The body of run_turn. Split out only so the metrics wrapper above stays
    readable; there is no second caller."""
    # Local rather than a parameter, unlike the accumulators above: nothing in
    # run_turn reads any of them back, so there is no reason to widen that
    # signature further.
    graph_checkpoint: dict | None = None
    graph_checkpoint_id: str | None = None
    # Last case_options event wins: the closing emit (chosen set) overwrites
    # the opening one, so what gets persisted is the resolved picker.
    case_options_data: dict | None = None

    events = (
        resume_chat_turn(case_pick[0], case_pick[1], conversation_id=conversation_id)
        if case_pick is not None
        else run_chat_turn(messages, conversation_id=conversation_id)
    )

    try:
        async for event in events:
            etype = event.get("type")
            if etype == "token":
                assistant_text += event.get("text", "")
            elif etype == "case_options":
                case_options_data = event.get("data")
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
            elif etype == "checkpoint":
                graph_checkpoint = event.get("data")
                graph_checkpoint_id = event.get("checkpoint_id")

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
        await turn_stream.emit(
            turn_id,
            {
                "type": "token",
                "text": "\n\nSomething went wrong generating your recommendation. Please try again.",
            },
        )

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
        await chat_buffer.save(
            conversation_id,
            {
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
                "graph_checkpoint": graph_checkpoint,
                "graph_checkpoint_id": graph_checkpoint_id,
                "case_options_data": case_options_data,
            },
        )

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
                graph_checkpoint,
                graph_checkpoint_id,
                rewound,
                case_options_data,
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

    # Build telemetry, persisted here rather than by the recorder that produced
    # it. The recorder runs inside the `build` graph node, and that path is not
    # allowed to write to Postgres — it buffers to Valkey and this drains it.
    # Outside the `persistable` branch on purpose: a guest turn's telemetry is
    # still worth having (it is GEPA training data, not user data), and it would
    # otherwise sit buffered until a job picked it up.
    await _drain_build_telemetry()

    await turn_stream.emit_end(turn_id)


async def _drain_build_telemetry() -> None:
    """Move buffered build telemetry into Postgres. Never raises.

    Deliberately not awaited for its result and never allowed to propagate: the
    turn is finished by this point, the user is not waiting on it, and a
    telemetry failure must not surface as a turn failure. Anything left behind
    stays buffered for the next turn or the drain job.
    """
    try:
        from app.services.recommender.recording import drain_pending

        await drain_pending()
    except Exception:
        logger.warning("build telemetry drain failed", exc_info=True)
