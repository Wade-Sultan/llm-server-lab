"""Guards the assistant-transport adapter.

WHY REPLAY-FROM-ZERO IS THE THING UNDER TEST. The old SSE adapter streamed
deltas and had the browser accumulate them, so resuming meant Last-Event-ID
bookkeeping on both sides. assistant-transport streams state snapshots instead,
which means a reconnect is just "replay the whole stream and rebuild" — and the
tests below exist to keep that property true. If the reader ever starts
depending on what a client already saw, resume quietly stops working for exactly
the case it exists for: a phone that locked during a three-minute build.

WHY THE USER'S OWN MESSAGE IS TESTED AT ALL. It looks like the client's job, and
it is not: the runtime drops its optimistic echo of the message the moment the
first state operation lands, and renders only what state says. A run that never
adds the message leaves the thread empty — which is the condition assistant-ui
puts the welcome screen back on screen for — and the next turn round-trips a
history with no user turns in it, a silent degradation of every elicitation
answer rather than a visible error.

WHY ONE TEST USES THE REAL `create_run`. Operations are deltas against the state
the *client* POSTed; nothing transmits the server's own copy. A fake controller
mutating a plain dict cannot see that distinction, and both real bugs here lived
in it — an index computed against the wrong base, and a `StateProxy` mistaken for
a plain `list`. See `test_the_browser_rebuilds_exactly_what_the_server_holds`.

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


def _drive(monkeypatch, *events, state=None, resuming=False, pending=None):
    monkeypatch.setattr(turn_stream, "tail", _events(*events))
    controller = _FakeController(
        state if state is not None else transport.initial_state()
    )
    asyncio.run(
        transport.stream_turn_into(
            controller, "turn-1", pending=pending, resuming=resuming
        )
    )
    return controller.state


def _user_command(text, role="user"):
    return {
        "type": "add-message",
        "message": {"role": role, "parts": [{"type": "text", "text": text}]},
    }


# ------------------------------------------------------- commands into state --


def test_the_users_own_message_is_put_into_state_by_the_run(monkeypatch):
    """The regression that put the welcome screen back on screen mid-send.

    The runtime drops its optimistic echo of the message as soon as the first
    operation arrives, and renders only what state says. A run that never adds
    the message leaves a thread with zero messages in it, which is exactly the
    condition assistant-ui renders ThreadWelcome for.
    """
    state = _drive(
        monkeypatch,
        {"type": "token", "text": "What resolution?"},
        state=transport.ensure_shape({"messages": [], "pipeline": None}),
        pending=transport.command_messages(
            [_user_command("I want a gaming PC for 1440p")]
        ),
    )

    assert state["messages"][0] == {
        "role": "user",
        "content": "I want a gaming PC for 1440p",
        "build": None,
        "case_options": None,
    }


def test_a_second_turn_still_carries_the_first_turns_question_and_answer():
    """The silent half of the same bug. With user turns missing from state, the
    extraction model never sees what the user actually said — it just answers
    worse, with nothing in any log to say why."""
    state = transport.ensure_shape(
        {
            "messages": [
                {"role": "user", "content": "i want a gaming pc"},
                {"role": "assistant", "content": "What resolution?"},
            ],
            "pipeline": None,
        }
    )
    pending = transport.command_messages([_user_command("1440p")])
    messages = transport.to_chat_messages(state["messages"] + pending)

    assert [m.content for m in messages] == [
        "i want a gaming pc",
        "What resolution?",
        "1440p",
    ]
    assert all(isinstance(m, ChatMessage) for m in messages)


def test_non_message_commands_are_ignored():
    pending = transport.command_messages(
        [
            {"type": "add-tool-result", "toolCallId": "t1", "result": {}},
            _user_command("hello"),
        ]
    )

    assert [m.content for m in transport.to_chat_messages(pending)] == ["hello"]


def test_an_empty_assistant_turn_is_dropped():
    """A turn cancelled mid-stream leaves one behind. Feeding it back would show
    the extraction model a blank assistant reply."""
    state = {
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": ""},
        ]
    }

    assert [m.content for m in transport.messages_from_state(state)] == ["hi"]


def test_a_malformed_state_does_not_crash_the_run():
    """The boundary with a browser. A bad shape must fail here, not deep inside
    the run with a KeyError."""
    assert transport.messages_from_state("not a dict") == []
    assert transport.messages_from_state(None) == []
    assert transport.ensure_shape("not a dict")["messages"] == []
    assert transport.ensure_shape({"messages": "nope"})["messages"] == []
    assert transport.command_messages([{"type": "add-message"}]) == []


# ------------------------------------------------------------ state mapping --


def test_tokens_accumulate_into_one_assistant_message(monkeypatch):
    state = _drive(
        monkeypatch,
        {"type": "token", "text": "Hello "},
        {"type": "token", "text": "world"},
        {"type": "done"},
    )

    assert state["messages"][-1]["role"] == "assistant"
    assert state["messages"][-1]["content"] == "Hello world"


def test_the_assistant_turn_is_appended_after_the_user_message(monkeypatch):
    """Order matters on screen, and the reply must not overwrite the prompt."""
    state = _drive(
        monkeypatch,
        {"type": "token", "text": "Sure"},
        pending=transport.command_messages([_user_command("build me a pc")]),
    )

    assert [(m["role"], m["content"]) for m in state["messages"]] == [
        ("user", "build me a pc"),
        ("assistant", "Sure"),
    ]


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

    assert state["messages"][-1]["build"] == {"label": "Custom Build"}
    assert state["pipeline"] is None


def test_a_build_stays_on_the_turn_that_produced_it(monkeypatch):
    """A top-level build slot re-attaches to whichever assistant message is last,
    so a follow-up question after a build drags the card down onto the reply."""
    state = _drive(
        monkeypatch,
        {"type": "build", "key": "custom_dspy", "data": {"label": "Custom Build"}},
        {"type": "token", "text": "Here it is."},
    )
    build_index = len(state["messages"]) - 1

    # A later turn, on the same conversation.
    state = _drive(
        monkeypatch,
        {"type": "token", "text": "Yes."},
        state=state,
        pending=transport.command_messages([_user_command("can it run VR?")]),
    )

    assert state["messages"][build_index]["build"] == {"label": "Custom Build"}
    assert state["messages"][-1]["content"] == "Yes."
    assert state["messages"][-1]["build"] is None


def test_case_options_land_on_the_streaming_message(monkeypatch):
    """The picker hangs off the turn that asked, like the build does."""
    opening = {"token": "tok-1", "chosen": None, "options": [{"name": "NZXT H5"}]}
    state = _drive(monkeypatch, {"type": "case_options", "data": opening})

    assert state["messages"][-1]["case_options"] == opening


def test_a_resolved_picker_updates_the_message_that_showed_it(monkeypatch):
    """The pick is a separate turn, and its `chosen` belongs to the EARLIER
    message — the one holding the cards being clicked — not to the new message
    the finished build streams into. Targeting the current message instead
    would render a second picker under the build."""
    options = [{"name": "NZXT H5"}, {"name": "Fractal North"}]
    state = _drive(
        monkeypatch,
        {
            "type": "case_options",
            "data": {"token": "tok-1", "chosen": None, "options": options},
        },
        {"type": "token", "text": "Pick a case."},
    )
    picker_index = len(state["messages"]) - 1

    # The turn the pick starts, on the same conversation.
    state = _drive(
        monkeypatch,
        {
            "type": "case_options",
            "data": {"token": "tok-1", "chosen": "Fractal North", "options": options},
        },
        {"type": "build", "data": {"label": "Custom Build"}},
        {"type": "token", "text": "Here it is."},
        state=state,
    )

    assert state["messages"][picker_index]["case_options"]["chosen"] == "Fractal North"
    # ...and the new message carries the build alone, with no picker of its own.
    assert state["messages"][-1]["build"] == {"label": "Custom Build"}
    assert state["messages"][-1]["case_options"] is None


def test_a_picker_for_an_unknown_token_lands_on_the_current_message(monkeypatch):
    """Falling back keeps a resolved picker visible rather than dropping it
    when the message that showed it is somehow absent."""
    state = _drive(
        monkeypatch,
        {
            "type": "case_options",
            "data": {"token": "tok-9", "chosen": "NZXT H5", "options": []},
        },
    )

    assert state["messages"][-1]["case_options"]["chosen"] == "NZXT H5"


def test_resuming_clears_stale_case_options_before_the_replay(monkeypatch):
    """The replay rebuilds from zero into the reused trailing message, so
    whatever picker the client already had must be wiped first — same contract
    as `content` and `build`."""
    stale = {
        "role": "assistant",
        "content": "partial",
        "build": None,
        "case_options": {"token": "tok-0", "chosen": None, "options": []},
    }
    state = _drive(
        monkeypatch,
        {"type": "token", "text": "rebuilt"},
        state={"messages": [stale], "pipeline": None},
        resuming=True,
    )

    assert state["messages"][-1]["content"] == "rebuilt"
    assert state["messages"][-1]["case_options"] is None


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


def test_a_turn_of_only_internal_events_is_reported_as_empty(monkeypatch):
    """Internal events are not evidence a worker produced anything renderable."""
    state = _drive(monkeypatch, {"type": "usage", "cost_usd": 0.42})

    assert "didn't start" in state["messages"][-1]["content"]


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
    assert resumed["messages"][-1]["build"] == {"label": "Custom Build"}


def test_resuming_rebuilds_the_partial_reply_instead_of_stacking_a_second_one(
    monkeypatch,
):
    """The client reconnects holding the half-finished text it already saw.
    Replay is from zero, so that text must be replaced, not appended to — and it
    must not be left behind above the finished reply."""
    partial = transport.initial_state()
    partial["messages"].append(
        {"role": "user", "content": "build me a pc", "build": None}
    )
    partial["messages"].append(
        {"role": "assistant", "content": "Here is ", "build": None}
    )

    state = _drive(
        monkeypatch,
        {"type": "token", "text": "Here is "},
        {"type": "token", "text": "your build."},
        state=partial,
        resuming=True,
    )

    assert [(m["role"], m["content"]) for m in state["messages"]] == [
        ("user", "build me a pc"),
        ("assistant", "Here is your build."),
    ]


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


# ------------------------------------------------- ops against a real client --


def _apply_ops(base, operations):
    """Apply wire operations the way the browser does.

    The client's own state is the base — `create_run` does not transmit the
    state it was given, so anything the server fails to emit an operation for
    simply is not there as far as the browser is concerned.
    """
    for op in operations:
        target = base
        for key in op["path"][:-1]:
            target = target[int(key)] if isinstance(target, list) else target[key]
        if not op["path"]:
            base = op["value"]
            continue
        last = op["path"][-1]
        if isinstance(target, list):
            index = int(last)
            if op["type"] == "append-text":
                target[index] += op["value"]
            elif index == len(target):
                target.append(op["value"])
            else:
                target[index] = op["value"]
        elif op["type"] == "append-text":
            target[last] = (target[last] or "") + op["value"]
        else:
            target[last] = op["value"]
    return base


def test_the_browser_rebuilds_exactly_what_the_server_holds(monkeypatch):
    """The integration a fake controller cannot check, and where the real bugs
    were: operations are deltas against the state the *client* POSTed, so an
    index computed from a different base silently writes to the wrong message.

    Runs the driver through the real create_run/StateManager rather than a stub,
    which is what makes it able to catch a proxy being mistaken for a plain dict.
    """
    import copy

    from assistant_stream import create_run

    client_state = {
        "messages": [
            {"role": "user", "content": "i want a gaming pc", "build": None},
            {"role": "assistant", "content": "What resolution?", "build": None},
        ],
        "pipeline": None,
    }
    server_state = copy.deepcopy(client_state)
    pending = transport.command_messages([_user_command("1440p")])

    monkeypatch.setattr(
        turn_stream,
        "tail",
        _events(
            {"type": "progress", "step": "gpu", "message": "Choosing GPU…"},
            {"type": "token", "text": "Here is your build."},
            {"type": "build", "data": {"label": "Custom Build"}},
        ),
    )

    async def _run():
        ops = []
        async for chunk in create_run(
            lambda c: transport.stream_turn_into(c, "turn-1", pending=pending),
            state=server_state,
        ):
            ops.extend(getattr(chunk, "operations", []))
        return ops

    rebuilt = _apply_ops(copy.deepcopy(client_state), asyncio.run(_run()))

    assert [(m["role"], m["content"]) for m in rebuilt["messages"]] == [
        ("user", "i want a gaming pc"),
        ("assistant", "What resolution?"),
        ("user", "1440p"),
        ("assistant", "Here is your build."),
    ]
    assert rebuilt["messages"][-1]["build"] == {"label": "Custom Build"}
    assert rebuilt["pipeline"] is None
    # The history the client round-trips next turn must be intact, or the
    # elicitation model answers without seeing what was asked of it.
    assert len(transport.messages_from_state(rebuilt)) == 4
