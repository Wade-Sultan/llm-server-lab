"""Guards the assistant-transport adapter.

WHY REPLAY-FROM-ZERO IS THE THING UNDER TEST. The old SSE adapter streamed
deltas and had the browser accumulate them, so resuming meant Last-Event-ID
bookkeeping on both sides. assistant-transport streams state snapshots instead,
which means a reconnect is just "replay the whole stream and rebuild" — and the
tests below exist to keep that property true. If the reader ever starts
depending on what a client already saw, resume quietly stops working for exactly
the case it exists for: a phone that locked during a three-minute build.

The event vocabulary these assert against is the one `run_chat_turn` has always
emitted. Nothing in the pipeline knows this layer exists, and these tests are
what keeps that boundary honest.
"""

from __future__ import annotations

import asyncio

from app.schemas.chat import ChatMessage
from app.services import transport, turn_stream


class _FakeController:
    """Enough of assistant_stream's RunController for the reader.

    `append_state_text` walks the same path assistant-stream would and mutates
    in place, so a wrong path shows up as a KeyError here rather than as an
    empty message in a browser.
    """

    def __init__(self, state=None):
        self.state = state
        self.is_cancelled = False

    def append_state_text(self, path, delta):
        target = self.state
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = (target[path[-1]] or "") + delta


def _events(*events):
    """A turn_stream.tail stand-in yielding (entry_id, event) pairs."""

    async def _tail(turn_id, last_id="0", **kwargs):
        for i, event in enumerate(events):
            yield (f"1-{i}", event)

    return _tail


def _drive(monkeypatch, *events, state=None):
    monkeypatch.setattr(turn_stream, "tail", _events(*events))
    controller = _FakeController(
        state if state is not None else transport.initial_state()
    )
    asyncio.run(transport.stream_turn_into(controller, "turn-1"))
    return controller.state


# ------------------------------------------------------------ state mapping --


def test_tokens_accumulate_into_one_assistant_message(monkeypatch):
    state = _drive(
        monkeypatch,
        {"type": "token", "text": "Hello "},
        {"type": "token", "text": "world"},
        {"type": "done"},
    )

    assert state["messages"][-1] == {"role": "assistant", "content": "Hello world"}


def test_progress_is_visible_while_the_turn_runs(monkeypatch):
    """The old adapter pushed this into a Zustand store as it parsed. As state,
    it survives a reconnect without the client having kept anything.

    Snapshotted mid-stream on purpose: the run clears `pipeline` when it ends,
    so asserting after the fact would only ever see the cleanup.
    """
    seen: list = []

    async def _tail(turn_id, last_id="0", **kwargs):
        yield ("1-0", {"type": "progress", "step": "resolving", "message": "Building…"})
        seen.append(controller.state["pipeline"])
        yield ("1-1", {"type": "token", "text": "done"})

    monkeypatch.setattr(turn_stream, "tail", _tail)
    controller = _FakeController(transport.initial_state())
    asyncio.run(transport.stream_turn_into(controller, "turn-1"))

    assert seen == [{"step": "resolving", "message": "Building…"}]
    # And cleared once the turn is over, so no spinner outlives it.
    assert controller.state["pipeline"] is None


def test_a_build_clears_the_progress_line(monkeypatch):
    """Left set, it would pin a spinner under a finished BuildCard."""
    state = _drive(
        monkeypatch,
        {"type": "progress", "step": "resolving", "message": "Building…"},
        {"type": "build", "key": "custom_dspy", "data": {"label": "Custom Build"}},
    )

    assert state["build"] == {"label": "Custom Build"}
    assert state["pipeline"] is None


def test_the_progress_line_is_cleared_even_when_a_turn_dies_mid_build(monkeypatch):
    """A turn that stops emitting must not leave a spinner running forever."""
    state = _drive(
        monkeypatch, {"type": "progress", "step": "resolving", "message": "Building…"}
    )

    assert state["pipeline"] is None


def test_internal_events_never_reach_the_client(monkeypatch):
    """usage carries OpenRouter spend; checkpoint carries graph state. Neither
    belongs in a browser, and `usage` leaking would expose cost data."""
    state = _drive(
        monkeypatch,
        {"type": "usage", "cost_usd": 0.42, "tokens_in": 100},
        {"type": "reference_estimate", "key": "ref", "data": {"secret": True}},
        {"type": "checkpoint", "checkpoint_id": "c1", "data": {"cp": "..."}},
        {"type": "token", "text": "hi"},
    )

    flat = repr(state)
    assert "0.42" not in flat
    assert "checkpoint" not in flat
    assert state["messages"][-1]["content"] == "hi"


# ----------------------------------------------------------------- resuming --


def test_a_replay_rebuilds_the_same_state_as_the_original_run(monkeypatch):
    """The property the whole resume design rests on."""
    events = (
        {"type": "progress", "step": "resolving", "message": "Building…"},
        {"type": "token", "text": "Here is "},
        {"type": "build", "key": "custom_dspy", "data": {"label": "Custom Build"}},
        {"type": "token", "text": "your build."},
    )

    first = _drive(monkeypatch, *events)
    # A reconnecting client sends back whatever state it had — including none.
    resumed = _drive(monkeypatch, *events, state=transport.initial_state())

    assert first == resumed
    assert resumed["messages"][-1]["content"] == "Here is your build."
    assert resumed["build"] == {"label": "Custom Build"}


def test_a_cancelled_reader_stops_without_touching_the_turn(monkeypatch):
    """The browser going away must not end the turn — it keeps running on its
    worker and stays resumable."""

    async def _tail(turn_id, last_id="0", **kwargs):
        yield ("1-0", {"type": "token", "text": "first"})
        yield ("1-1", {"type": "token", "text": " second"})

    monkeypatch.setattr(turn_stream, "tail", _tail)
    controller = _FakeController(transport.initial_state())

    original = controller.append_state_text

    def _cancel_after_first(path, delta):
        original(path, delta)
        controller.is_cancelled = True

    controller.append_state_text = _cancel_after_first
    asyncio.run(transport.stream_turn_into(controller, "turn-1"))

    assert controller.state["messages"][-1]["content"] == "first"


def test_an_empty_stream_says_so_rather_than_hanging(monkeypatch):
    """No worker ever wrote to this stream. A blank reply with nothing in any
    log to explain it is the failure this refuses to produce."""
    state = _drive(monkeypatch)

    assert "didn't start" in state["messages"][-1]["content"]


# --------------------------------------------------------------- commands --


def test_history_and_the_new_turn_combine_in_order():
    state = {
        "messages": [
            {"role": "user", "content": "i want a gaming pc"},
            {"role": "assistant", "content": "What resolution?"},
        ]
    }
    commands = [
        {
            "type": "add-message",
            "message": {"role": "user", "parts": [{"type": "text", "text": "1440p"}]},
        }
    ]

    messages = transport.messages_from_commands(commands, state)

    assert [m.content for m in messages] == [
        "i want a gaming pc",
        "What resolution?",
        "1440p",
    ]
    assert all(isinstance(m, ChatMessage) for m in messages)


def test_an_empty_assistant_turn_is_dropped():
    """A turn cancelled mid-stream leaves one behind. Feeding it back would show
    the extraction model a blank assistant reply."""
    state = {
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": ""},
        ]
    }

    assert [m.content for m in transport.messages_from_commands([], state)] == ["hi"]


def test_non_message_commands_are_ignored():
    commands = [
        {"type": "add-tool-result", "toolCallId": "t1", "result": {}},
        {
            "type": "add-message",
            "message": {"role": "user", "parts": [{"type": "text", "text": "hello"}]},
        },
    ]

    assert [m.content for m in transport.messages_from_commands(commands, None)] == [
        "hello"
    ]


def test_a_malformed_state_does_not_crash_the_run():
    """The boundary with a browser. A bad shape must fail here, not deep inside
    the run with a KeyError."""
    assert transport.messages_from_commands([], "not a dict") == []
    assert transport.messages_from_commands([], None) == []
