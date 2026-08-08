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


def initial_state(messages: list[ChatMessage] | None = None) -> dict[str, Any]:
    """The state shape both ends agree on.

    `build` and `pipeline` are first-class state rather than side effects, which
    is the substantive change from the SSE adapter — a resumed run reconstructs
    the BuildCard and the progress line for free, because they were never
    client-side accumulations to begin with.
    """
    return {
        "messages": [{"role": m.role, "content": m.content} for m in (messages or [])],
        "build": None,
        "pipeline": None,
    }


def _ensure_shape(state: Any) -> dict[str, Any]:
    """Coerce whatever the client sent into the shape the callback mutates.

    The client round-trips state it was given, so this is normally a no-op — but
    it is the boundary with a browser, and a missing `messages` list would fail
    deep inside the run with a KeyError rather than here.
    """
    if not isinstance(state, dict):
        return initial_state()
    state.setdefault("messages", [])
    state.setdefault("build", None)
    state.setdefault("pipeline", None)
    return state


async def stream_turn_into(
    controller: RunController,
    turn_id: str,
    *,
    replay_from: str = "0",
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
    """
    controller.state = _ensure_shape(controller.state)

    # The assistant turn being built. Appended once up front so text can stream
    # into it in place — assistant-stream diffs the state tree, so mutating this
    # dict is what produces token-by-token output on the client.
    assistant: dict[str, Any] = {"role": "assistant", "content": ""}
    controller.state["messages"].append(assistant)
    index = len(controller.state["messages"]) - 1
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
            etype = event.get("type")
            if etype in _INTERNAL_EVENTS:
                continue
            saw_event = True

            if etype == "token":
                text = event.get("text", "")
                if text:
                    # Appended through the controller rather than by rewriting
                    # the string, so the wire carries the delta instead of the
                    # whole message again on every token.
                    controller.append_state_text(["messages", index, "content"], text)
            elif etype == "progress":
                controller.state["pipeline"] = {
                    "step": event.get("step"),
                    "message": event.get("message"),
                }
            elif etype == "build":
                controller.state["build"] = event.get("data")
                # The build has landed, so there is no step in flight any more.
                # Left set, it would pin the progress line under a finished card.
                controller.state["pipeline"] = None

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
) -> None:
    """Drive a run directly from the pipeline, with no Valkey in between.

    The local-development and no-Valkey path. It is the only chat path exercised
    in the test suite and on a laptop, so it has to keep working — but it is not
    a production mode: the turn dies with this request, which is precisely what
    dispatch exists to prevent.
    """
    from app.services.chat_pipeline import run_chat_turn

    controller.state = _ensure_shape(controller.state)
    assistant: dict[str, Any] = {"role": "assistant", "content": ""}
    controller.state["messages"].append(assistant)
    index = len(controller.state["messages"]) - 1

    try:
        async for event in run_chat_turn(messages, conversation_id=conversation_id):
            etype = event.get("type")
            if etype in _INTERNAL_EVENTS:
                continue
            if etype == "token":
                if text := event.get("text", ""):
                    controller.append_state_text(["messages", index, "content"], text)
            elif etype == "progress":
                controller.state["pipeline"] = {
                    "step": event.get("step"),
                    "message": event.get("message"),
                }
            elif etype == "build":
                controller.state["build"] = event.get("data")
                controller.state["pipeline"] = None
    except Exception:
        logger.exception("inline transport run failed")
        controller.append_state_text(
            ["messages", index, "content"],
            "\n\nSomething went wrong generating your recommendation. "
            "Please try again.",
        )
    finally:
        controller.state["pipeline"] = None


def messages_from_commands(commands: list[dict], state: Any) -> list[ChatMessage]:
    """Build the conversation the pipeline runs against.

    The history comes from the state the client round-tripped; the new turn comes
    from this request's `add-message` commands. Assistant messages with empty
    content are dropped — a turn that was cancelled mid-stream leaves one behind,
    and feeding it back would show the extraction model a blank assistant reply.
    """
    out: list[ChatMessage] = []
    if isinstance(state, dict):
        for msg in state.get("messages") or []:
            role = msg.get("role")
            content = msg.get("content") or ""
            if role in ("user", "assistant") and content.strip():
                out.append(ChatMessage(role=role, content=content))

    for command in commands:
        if command.get("type") != "add-message":
            continue
        message = command.get("message") or {}
        text = "\n".join(
            part.get("text", "")
            for part in message.get("parts") or []
            if part.get("type") == "text"
        ).strip()
        if text:
            out.append(ChatMessage(role=message.get("role", "user"), content=text))
    return out
