"""assistant-transport adapter: turn events become server-authoritative state.

WHAT CHANGED, CONCEPTUALLY. The old SSE contract streamed *deltas* and left the
browser to accumulate them — `model-adapter.ts` held `fullText`, pushed progress
into one Zustand store and the BuildCard into another, and re-yielded the whole
content array on every token. assistant-transport inverts that: the server owns
the state, mutations to `controller.state` are diffed and streamed, and the
client renders whatever it is told.

That inversion is what makes resume cheap. A reconnecting browser does not need
to have kept anything, and the server does not need to know what it missed —
replaying the turn's Valkey stream from the beginning rebuilds the same state,
because `turn_stream.tail(turn_id, last_id="0")` was always able to replay from
zero. The Last-Event-ID bookkeeping that used to exist on both sides is gone.

SERVER-AUTHORITATIVE MEANS *ALL* OF IT, INCLUDING THE USER'S OWN MESSAGE. The
runtime clears its optimistic echo of the message just typed as soon as the first
state operation arrives, and it renders only what state says. So the message has
to be put *into* state by this module — `_append_pending` does it, and
`messages_from_state` then reads the whole conversation back out of one place.
Leaving it out does not merely lose a bubble on screen: the thread renders empty
and drops back to the welcome screen mid-send, and the next turn round-trips a
history with no user turns in it, so the elicitation model silently answers
without ever seeing what was asked of it.

OPERATIONS ARE DELTAS AGAINST THE CLIENT'S OWN STATE. `create_run(state=...)`
does not transmit that state — the browser uses the state it POSTed as the base
and applies our ops on top (`AssistantMessageAccumulator`'s `initialMessage` is
built from its own `agentStateRef`). Two consequences, both load-bearing:
mutations have to go through `controller.state` so they emit ops, and the base
we mutate has to be exactly what the client sent, or every index in those ops
points at the wrong message.

THE PIPELINE IS UNTOUCHED. This module reads the same event vocabulary
`run_chat_turn` has always emitted (progress / token / build / done). Nothing
about dispatch, the worker, or the DSPy pipeline knows this layer exists.
"""

from __future__ import annotations

import logging
from typing import Any

from assistant_stream import RunController

from app.schemas.chat import ChatMessage
from app.services import turn_stream

logger = logging.getLogger(__name__)

# Events that exist for the server's benefit and must never reach a browser:
# `usage` carries OpenRouter spend, `reference_estimate` is a caching signal,
# `checkpoint` is the graph state mirrored into Postgres.
_INTERNAL_EVENTS = frozenset({"usage", "reference_estimate", "checkpoint"})


def _message(role: str, content: str = "") -> dict[str, Any]:
    """One message in the shape both ends agree on.

    `build` hangs off the message that produced it rather than sitting in a
    top-level slot. A conversation can contain several builds, and a top-level
    slot both loses the older ones and re-attaches the survivor to whichever
    assistant message happens to be last — so asking a follow-up question after
    a build would drag the card down onto the reply.

    `case_options` hangs off the message for the same reason: it is the case
    picker shown mid-build, and it must stay on the turn that asked.
    """
    return {"role": role, "content": content, "build": None, "case_options": None}


def initial_state(messages: list[ChatMessage] | None = None) -> dict[str, Any]:
    """The state shape both ends agree on.

    `build` and `pipeline` are first-class state rather than side effects, which
    is the substantive change from the SSE adapter — a resumed run reconstructs
    the BuildCard and the progress line for free, because they were never
    client-side accumulations to begin with.
    """
    return {
        "messages": [_message(m.role, m.content) for m in (messages or [])],
        "pipeline": None,
    }


def ensure_shape(state: Any) -> dict[str, Any]:
    """Coerce whatever the client sent into the shape the drivers mutate.

    The client round-trips state it was given, so this is normally a no-op — but
    it is the boundary with a browser, and a missing `messages` list would fail
    deep inside the run with a KeyError rather than here.

    CALL THIS ON THE PLAIN DICT, BEFORE `create_run` — never on a live
    `controller.state`. That is a `StateProxy`, and its `__getitem__` returns
    further proxies rather than the underlying list, so `isinstance(messages,
    list)` reads False against a perfectly good message list and this function
    would replace it with an empty one. The visible result is the bug this whole
    change exists to fix: the opening root `set` wipes the user's message and the
    thread renders as empty.
    """
    if not isinstance(state, dict):
        return initial_state()
    messages = state.get("messages")
    state["messages"] = messages if isinstance(messages, list) else []
    state.setdefault("pipeline", None)
    return state


def _command_text(message: dict[str, Any]) -> str:
    """The text of an `add-message` command, flattened across its parts."""
    return "\n".join(
        part.get("text", "")
        for part in message.get("parts") or []
        if isinstance(part, dict) and part.get("type") == "text"
    ).strip()


def command_messages(commands: list[dict]) -> list[dict[str, Any]]:
    """The messages this request's `add-message` commands are asking us to add.

    Returned rather than folded into the state here, because they have to be
    appended *through the controller* once the run starts — see the note on
    deltas at the top of this module. Appending them to the plain dict
    beforehand would leave the client's copy without them and shift every
    subsequent message index by one.
    """
    out: list[dict[str, Any]] = []
    for command in commands or []:
        if not isinstance(command, dict) or command.get("type") != "add-message":
            continue
        message = command.get("message") or {}
        if text := _command_text(message):
            out.append(_message(message.get("role", "user"), text))
    return out


def rewind_prefix(
    messages: list[Any], parent_id: str | None
) -> list[dict[str, Any]] | None:
    """The messages that survive an edit, or None to keep the whole history.

    WHAT `parentId` IS. assistant-transport has no "edit" command — editing a
    message sends the same `add-message` any send does, and the only thing that
    marks it as an edit is `parentId`: the id of the message the new one is
    being appended *after*. So replacing history rather than appending to it is
    a decision made here, not in the browser.

    The ids are array indices. `convertExternalMessages` labels each converted
    message with its position (`fromThreadMessageLike(joined, idx.toString())`),
    and our converter emits exactly one message per state message, so index i in
    this list is id "i". Editing message i therefore arrives as parent "i-1",
    and everything from i onward — the message and every turn it led to — is
    what the edit discards. A null parent means the first message was edited and
    nothing survives.

    Returns None, meaning "no rewind", for the ordinary send (parent is the last
    message, so there is nothing to drop) and for any parent that does not parse
    as an index in range. That degradation is deliberate: an unrecognised parent
    should append like it always did, never silently delete a conversation.
    """
    if not messages:
        return None
    if parent_id is None:
        return []

    try:
        parent_index = int(parent_id)
    except (TypeError, ValueError):
        return None

    keep = parent_index + 1
    if keep < 0 or keep >= len(messages):
        return None
    return list(messages[:keep])


def to_chat_messages(messages: Any) -> list[ChatMessage]:
    """Convert state messages into what the pipeline consumes.

    Assistant messages with empty content are dropped — a turn cancelled
    mid-stream leaves one behind, and feeding it back would show the extraction
    model a blank assistant reply.
    """
    out: list[ChatMessage] = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content") or ""
        if role in ("user", "assistant") and content.strip():
            out.append(ChatMessage(role=role, content=content))
    return out


def messages_from_state(state: Any) -> list[ChatMessage]:
    """The conversation so far, as the pipeline consumes it."""
    if not isinstance(state, dict):
        return []
    return to_chat_messages(state.get("messages"))


def _rewind(controller: RunController, keep: list[dict[str, Any]] | None) -> None:
    """Drop the messages an edit discards, as an operation.

    Through `controller.state` for the same reason `_append_pending` is: the
    client applies our ops to the state it POSTed, so a truncation done to the
    plain dict before `create_run` would leave the browser still holding the
    replaced messages while every subsequent index we send points one place too
    far along.

    A whole-list `set` rather than a removal because the op vocabulary is only
    `set` and `append-text` — see assistant_stream/state.py. `keep` must be
    plain values sliced from the request, never read back out of
    `controller.state`, whose `__getitem__` hands back further proxies.
    """
    if keep is None:
        return
    controller.state["messages"] = keep


def _append_pending(controller: RunController, pending: list[dict[str, Any]]) -> None:
    """Put this turn's incoming messages into state, as operations.

    Through `controller.state` on purpose: that is what emits `set` ops the
    browser can apply. It is also what keeps the user's message on screen — the
    runtime drops its optimistic echo the moment the first operation lands.
    """
    for message in pending:
        controller.state["messages"].append(message)


def _begin_assistant_turn(controller: RunController, *, resuming: bool = False) -> int:
    """Open the assistant message this turn streams into, and return its index.

    Appended up front so text can stream into it in place — assistant-stream
    diffs the state tree, so mutating this dict is what produces token-by-token
    output on the client.

    Resuming reuses a trailing assistant message rather than appending a second
    one. The replay rebuilds content from zero, so the partial text the client
    already has must be cleared instead of appended to; without the reuse, a
    reconnect leaves the half-finished first attempt sitting above the complete
    one.
    """
    messages = controller.state["messages"]
    if resuming and len(messages) > 0 and messages[-1].get("role") == "assistant":
        index = len(messages) - 1
        messages[index]["content"] = ""
        messages[index]["build"] = None
        messages[index]["case_options"] = None
        return index

    messages.append(_message("assistant"))
    return len(messages) - 1


def _apply_event(controller: RunController, index: int, event: dict) -> bool:
    """Map one pipeline event onto state. False means it was internal.

    Shared by the streamed and inline drivers so the two cannot drift — they are
    the same contract reached by different routes, and a divergence would show up
    only on whichever path the test suite does not exercise.
    """
    etype = event.get("type")
    if etype in _INTERNAL_EVENTS:
        return False

    if etype == "token":
        if text := event.get("text", ""):
            # Appended through the controller rather than by rewriting the
            # string, so the wire carries the delta instead of the whole message
            # again on every token.
            controller.append_state_text(["messages", index, "content"], text)
    elif etype == "progress":
        controller.state["pipeline"] = {
            "step": event.get("step"),
            "message": event.get("message"),
        }
    elif etype == "build":
        controller.state["messages"][index]["build"] = event.get("data")
        # The build has landed, so there is no step in flight any more. Left
        # set, it would pin the progress line under a finished card.
        controller.state["pipeline"] = None
    elif etype == "case_options":
        _apply_case_options(controller, index, event.get("data") or {})
    return True


def _apply_case_options(
    controller: RunController, index: int, data: dict[str, Any]
) -> None:
    """Put the case picker on the message it belongs to.

    Emitted twice, and by two different turns. The turn that pauses emits it
    open (`chosen` null) and it belongs to that turn's own message. The turn
    the user's pick starts emits it resolved — and that one belongs to the
    EARLIER message, the one showing the cards being clicked, not to the new
    message the finished build is streaming into.

    So the target is found by token rather than assumed to be the current
    message. Falling back to the current index keeps a resolved picker whose
    original message is somehow absent visible rather than silently dropped.
    """
    token = data.get("token")
    messages = controller.state["messages"]
    if token:
        for i in range(len(messages) - 1, -1, -1):
            existing = messages[i].get("case_options")
            if existing and existing.get("token") == token:
                messages[i]["case_options"] = data
                return
    messages[index]["case_options"] = data


async def stream_turn_into(
    controller: RunController,
    turn_id: str,
    *,
    pending: list[dict[str, Any]] | None = None,
    keep: list[dict[str, Any]] | None = None,
    replay_from: str = "0",
    resuming: bool = False,
) -> None:
    """Drive a run's state from a turn's Valkey event stream.

    Always replays from the beginning by default. That is not laziness about
    resumption — it is the mechanism: state snapshots are absolute, so rebuilding
    from zero is both the first attach and the reconnect, with no delta
    bookkeeping to get wrong in either direction.

    Never raises. A turn whose stream cannot be read still ends with an
    apologetic assistant message rather than a hung request, for the same reason
    turn_runner writes one: a browser waiting on a stream that never terminates
    is worse than a visible error.

    `controller.state` is assumed already shaped — see `ensure_shape`, which the
    route calls on the plain dict before handing it to `create_run`.
    """
    # Before the append, so the pending message lands at the index the rewound
    # history actually ends at rather than after the messages it replaces.
    _rewind(controller, keep)
    _append_pending(controller, pending or [])
    index = _begin_assistant_turn(controller, resuming=resuming)
    saw_event = False

    try:
        async for item in turn_stream.tail(turn_id, last_id=replay_from):
            if controller.is_cancelled:
                # The browser went away. The turn keeps running on its worker
                # and stays resumable; only this reader stops.
                logger.info("transport reader for turn %s cancelled", turn_id)
                return
            if item is None:
                # Idle round. DataStreamResponse's own heartbeat keeps the
                # connection warm, so there is nothing to emit here.
                continue

            _entry_id, event = item
            saw_event = _apply_event(controller, index, event) or saw_event

        if not saw_event:
            # tail() also returns when it gives up waiting for a terminal entry,
            # which means no worker ever wrote to this stream. Indistinguishable
            # from a normal finish except by having produced nothing.
            logger.warning(
                "transport reader for turn %s saw no events — no worker wrote "
                "to this stream. Check the worker Deployment and that both pods "
                "point at the same Valkey.",
                turn_id,
            )
            controller.append_state_text(
                ["messages", index, "content"],
                "That build didn't start. Please try again.",
            )
    except Exception:
        logger.exception("transport reader failed for turn %s", turn_id)
        controller.append_state_text(
            ["messages", index, "content"],
            "\n\nSomething went wrong generating your recommendation. "
            "Please try again.",
        )
    finally:
        controller.state["pipeline"] = None


async def run_turn_inline_into(
    controller: RunController,
    messages: list[ChatMessage],
    conversation_id: str | None,
    *,
    pending: list[dict[str, Any]] | None = None,
    keep: list[dict[str, Any]] | None = None,
    case_pick: tuple[str, str] | None = None,
) -> None:
    """Drive a run directly from the pipeline, with no Valkey in between.

    The local-development and no-Valkey path. It is the only chat path exercised
    in the test suite and on a laptop, so it has to keep working — but it is not
    a production mode: the turn dies with this request, which is precisely what
    dispatch exists to prevent.

    A `case_pick` turn resumes a paused build instead of running the graph.
    Note what that means without Valkey: the pause was still saved, because
    paused_build writes to Postgres first and treats Valkey as the cache. So
    the picker works on a laptop even though nothing else about a turn survives
    the request that started it.
    """
    from app.services.chat_pipeline import resume_chat_turn, run_chat_turn

    _rewind(controller, keep)
    _append_pending(controller, pending or [])
    index = _begin_assistant_turn(controller)

    events = (
        resume_chat_turn(case_pick[0], case_pick[1], conversation_id=conversation_id)
        if case_pick is not None
        else run_chat_turn(messages, conversation_id=conversation_id)
    )

    try:
        async for event in events:
            _apply_event(controller, index, event)
    except Exception:
        logger.exception("inline transport run failed")
        controller.append_state_text(
            ["messages", index, "content"],
            "\n\nSomething went wrong generating your recommendation. "
            "Please try again.",
        )
    finally:
        controller.state["pipeline"] = None
