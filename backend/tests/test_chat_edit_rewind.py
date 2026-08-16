"""Editing a message rewrites history rather than appending to it.

WHY THIS NEEDS A TEST AT THE HTTP BOUNDARY. assistant-transport has no "edit"
command. An edit arrives as exactly the same `add-message` any send does, and
the only thing distinguishing it is a sibling `parentId` field naming the
message the new one goes after. So "this is an edit" is a server-side inference
from one field, and every guard around it — the alias that field parses under,
the absent-versus-null distinction, the refusal to rewind on a parent that does
not resolve — is load bearing in a way that unit-testing `rewind_prefix` alone
would not catch. The dangerous failure is silent and destructive: read a normal
send as an edit and the turn deletes the conversation it was appending to.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services import transport
from app.services.turn_runner import messages_to_drop, messages_to_write

U, A = "user", "assistant"


def _state(*contents: str) -> dict[str, Any]:
    """State as the client round-trips it: alternating user/assistant."""
    return {
        "messages": [
            {"role": U if i % 2 == 0 else A, "content": c, "build": None}
            for i, c in enumerate(contents)
        ],
        "pipeline": None,
    }


def _command(text: str) -> dict[str, Any]:
    return {
        "type": "add-message",
        "message": {"role": U, "parts": [{"type": "text", "text": text}]},
    }


class Dispatches:
    """The chat route with its dispatch captured instead of reaching Pub/Sub.

    Only the published payload is asserted on. It is written before streaming
    begins, so the response body — which would otherwise read a Valkey stream
    nobody is writing to — never has to be consumed.
    """

    def __init__(self, client: TestClient) -> None:
        self._client = client
        self.payloads: list[dict] = []

    def post(self, body: dict) -> None:
        self._client.post("/api/v1/chat", json=body)

    def one(self) -> dict:
        assert len(self.payloads) == 1, self.payloads
        return self.payloads[0]


@pytest.fixture
def dispatches(monkeypatch) -> Dispatches:
    from app.api.routes import chat as chat_route

    app = FastAPI()
    app.include_router(chat_route.router, prefix="/api/v1")
    captured = Dispatches(TestClient(app))

    async def fake_publish(_turn_id, _conversation_id, payload) -> bool:
        captured.payloads.append(payload)
        return True

    async def fake_valkey() -> bool:
        return True

    async def noop(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(chat_route.pubsub, "publish_turn", fake_publish)
    monkeypatch.setattr(chat_route.pubsub, "is_enabled", lambda: True)
    monkeypatch.setattr(chat_route, "valkey_available", fake_valkey)
    monkeypatch.setattr(chat_route.turn_stream, "set_active_turn", noop)
    return captured


# -- the parentId contract at the HTTP boundary ----------------------------


def test_an_edit_sends_the_rewritten_history_to_the_worker(dispatches) -> None:
    """Editing "1440p" drops it and the reply it produced, then re-asks."""
    dispatches.post(
        {
            "commands": [_command("4k please")],
            "state": _state("1440p", "What budget?"),
            "parentId": None,  # the edited message was the first one
        },
    )

    payload = dispatches.one()
    assert [m["content"] for m in payload["messages"]] == ["4k please"]
    assert payload["rewound"] is True


def test_editing_a_later_message_keeps_everything_before_it(dispatches) -> None:
    dispatches.post(
        {
            "commands": [_command("actually, 4k")],
            "state": _state("1440p", "What budget?", "$2000", "Here you go"),
            "parentId": "1",  # edit message 2, keep 0 and 1
        },
    )

    payload = dispatches.one()
    assert [m["content"] for m in payload["messages"]] == [
        "1440p",
        "What budget?",
        "actually, 4k",
    ]
    assert payload["rewound"] is True


def test_an_ordinary_send_does_not_rewind(dispatches) -> None:
    """The runtime sends parentId on every turn, not just edits.

    A normal send names the last message as its parent, which resolves to "keep
    everything" — so the flag must come out False or every turn would tell the
    worker to start deleting.
    """
    dispatches.post(
        {
            "commands": [_command("$2000")],
            "state": _state("1440p", "What budget?"),
            "parentId": "1",
        },
    )

    payload = dispatches.one()
    assert [m["content"] for m in payload["messages"]] == [
        "1440p",
        "What budget?",
        "$2000",
    ]
    assert payload["rewound"] is False


def test_an_absent_parent_id_is_not_read_as_a_null_one(dispatches) -> None:
    """The distinction that protects a conversation from being wiped.

    `parentId: null` means "the first message was edited, keep nothing". A body
    with no parentId at all means the client said nothing about parentage, and
    conflating the two would let such a request delete the entire history.
    """
    dispatches.post(
        {
            "commands": [_command("$2000")],
            "state": _state("1440p", "What budget?"),
        },
    )

    payload = dispatches.one()
    assert [m["content"] for m in payload["messages"]] == [
        "1440p",
        "What budget?",
        "$2000",
    ]
    assert payload["rewound"] is False


def test_the_unaliased_spelling_does_not_rewind(dispatches) -> None:
    """`parent_id` is not `parentId`, and must not be mistaken for a null one.

    The field parses under its alias only, so an unaliased `parent_id` leaves it
    None. But `extra` is "allow", so that key is kept as an extra and its name
    joins `model_fields_set` — making a set-ness test alone read this body as
    "parent is null, keep nothing" and delete the history it meant to append to.
    Any value at all triggers it, including one that would never resolve to an
    index, so the unresolvable-parent guard below is no protection either.
    """
    dispatches.post(
        {
            "commands": [_command("$2000")],
            "state": _state("1440p", "What budget?"),
            "parent_id": "1",
        },
    )

    payload = dispatches.one()
    assert [m["content"] for m in payload["messages"]] == [
        "1440p",
        "What budget?",
        "$2000",
    ]
    assert payload["rewound"] is False


def test_an_unresolvable_parent_id_appends_instead_of_deleting(dispatches) -> None:
    """Degrade to the old behaviour, never to data loss."""
    dispatches.post(
        {
            "commands": [_command("$2000")],
            "state": _state("1440p", "What budget?"),
            "parentId": "not-an-index",
        },
    )

    payload = dispatches.one()
    assert [m["content"] for m in payload["messages"]] == [
        "1440p",
        "What budget?",
        "$2000",
    ]
    assert payload["rewound"] is False


# -- rewind_prefix directly ------------------------------------------------


def test_rewind_prefix_keeps_the_prefix_up_to_and_including_the_parent() -> None:
    messages = _state("a", "b", "c", "d")["messages"]
    assert [m["content"] for m in transport.rewind_prefix(messages, "1")] == ["a", "b"]
    assert transport.rewind_prefix(messages, None) == []


@pytest.mark.parametrize("parent", ["3", "99", "-2", "abc", ""])
def test_rewind_prefix_declines_rather_than_guesses(parent: str) -> None:
    """Out of range, unparseable, or already the last message: keep everything."""
    messages = _state("a", "b", "c", "d")["messages"]
    assert transport.rewind_prefix(messages, parent) is None


def test_rewind_prefix_treats_minus_one_as_the_parent_of_the_first_message() -> None:
    """ "-1" must keep nothing, exactly as a null parent does.

    THE RETRY BUTTON DEPENDS ON THIS and cannot express it any other way. It
    re-runs a turn by calling the runtime's `append` with the parent of the user
    message being retried, but assistant-ui's `toAppendMessage` resolves
    `message.parentId ?? messages.at(-1)?.id` — and `??` treats null as absent.
    A null parent would therefore be rewritten to the thread's TAIL, which
    routes to onNew and appends a duplicate message instead of retrying.

    So retrying the first turn sends "-1" instead: one before index 0, non-null
    so it survives `??`, and `keep = int(parent) + 1 = 0` here. Note "-2" is in
    the declining case above — only exactly one before the start is meaningful.
    """
    messages = _state("a", "b", "c", "d")["messages"]
    assert transport.rewind_prefix(messages, "-1") == []
    assert transport.rewind_prefix(messages, "-1") == transport.rewind_prefix(
        messages, None
    )


# -- what persistence does with a rewound turn -----------------------------


def test_persistence_drops_exactly_the_replaced_rows() -> None:
    stored = [(U, "1440p"), (A, "What budget?"), (U, "$2000"), (A, "Here you go")]
    incoming = [(U, "1440p"), (A, "What budget?"), (U, "$3000"), (A, "Revised")]

    assert messages_to_write(stored, incoming) == [(U, "$3000"), (A, "Revised")]
    assert messages_to_drop(stored, incoming) == [(U, "$2000"), (A, "Here you go")]


def test_persistence_drops_nothing_for_an_ordinary_append() -> None:
    stored = [(U, "1440p"), (A, "What budget?")]
    assert messages_to_drop(stored, [*stored, (U, "$2000"), (A, "Here you go")]) == []


def test_persistence_drops_nothing_for_a_merely_reordered_conversation() -> None:
    """The safety property `save_turn` relies on beyond its `rewound` gate.

    Conversations scrambled by the old count-based bug have their rows out of
    order but present. The shared matching loop consumes those as matches, so
    even a turn that did rewind cannot delete a message it still contains.
    """
    stored = [(A, "What budget?"), (U, "1440p")]
    incoming = [(U, "1440p"), (A, "What budget?")]
    assert messages_to_drop(stored, incoming) == []
