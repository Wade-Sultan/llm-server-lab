"""Guards the chat turn graph: what it asks, when it hands off, and what it emits.

TWO THINGS ARE LOAD-BEARING HERE.

First, `is_profile_complete()` is the only thing allowed to decide the turn is
ready to build. The router calls a model, but only ever to pick which of several
already-known-missing items to raise next. If that boundary ever slips — if the
model's answer can shorten intake or trigger a build — a hallucination stops
costing an awkward question and starts costing a build recommended against a
profile the user never stated.

Second, the SSE event vocabulary. Three callers and the frontend consume
run_chat_turn's events positionally and by name; the graph rewrite was supposed
to be invisible to all of them. The end-to-end tests below assert the exact
sequence, because "the graph still works" and "the browser still renders" are
different claims.

No network: every LLM call is stubbed, and the graph runs on an in-memory
checkpointer.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.schemas.chat import BuildProfile, ChatMessage
from app.services import chat_pipeline as cp
from app.services.graph import graph as graph_mod
from app.services.graph import nodes
from app.services.graph.state import merge_profile, new_usage


def _profile(**overrides) -> dict:
    base = {
        "primary_use": "gaming",
        "budget_tier": "mid",
        "price_sensitivity": "firm",
        "gaming_resolution": "1440p",
        "gaming_fps": "144",
    }
    base.update(overrides)
    return BuildProfile(**base).model_dump()


def _state(profile: dict, **overrides) -> dict:
    state = {
        "messages": [{"role": "user", "content": "i want a gaming pc"}],
        "conversation_id": None,
        "session_id": "s-1",
        "profile": profile,
        "usage": new_usage(),
        "asked_fields": [],
    }
    state.update(overrides)
    return state


class StubChoice:
    def __init__(self, content: str) -> None:
        self.message = SimpleNamespace(content=content)


class StubClient:
    """An OpenRouter client that returns one canned completion and counts calls."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        return SimpleNamespace(
            choices=[StubChoice(self.reply)],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=1, cost=0.0001),
            model="stub/router",
        )


@pytest.fixture
def stub_router(monkeypatch):
    def _install(reply: str) -> StubClient:
        client = StubClient(reply)
        monkeypatch.setattr(cp, "_get_client", lambda: client)
        return client

    return _install


# ----------------------------------------------------------- the readiness gate --


def test_a_complete_profile_routes_to_build_without_calling_a_model(stub_router):
    """The handoff is a code decision. No model is consulted, at all."""
    client = stub_router("1")
    result = asyncio.run(nodes.route(_state(_profile())))

    assert result["next_question"] is None
    assert nodes.should_build({**result}) == "build"
    assert client.calls == 0


def test_an_incomplete_profile_routes_to_ask(stub_router):
    stub_router("1")
    state = _state(_profile(gaming_fps=None, budget_tier="unknown"))
    result = asyncio.run(nodes.route(state))

    assert result["next_question"] is not None
    assert nodes.should_build({**result}) == "ask"


def test_the_router_can_only_choose_from_the_missing_list(stub_router):
    """It returns an index, so it cannot invent a field or skip intake."""
    stub_router("2")
    profile = _profile(gaming_resolution=None, gaming_fps=None)
    state = _state(profile)

    result = asyncio.run(nodes.route(state))
    expected = cp._missing_fields(BuildProfile(**profile))

    assert result["next_question"] in expected
    assert result["next_question"] == expected[1]


@pytest.mark.parametrize("reply", ["", "the budget one", "99", "0", "-1", "banana"])
def test_an_unusable_router_reply_falls_back_to_priority_order(stub_router, reply):
    """Every bad answer degrades to the hardcoded ordering this replaced."""
    stub_router(reply)
    profile = _profile(gaming_resolution=None, gaming_fps=None)
    result = asyncio.run(nodes.route(_state(profile)))

    assert result["next_question"] == cp._missing_fields(BuildProfile(**profile))[0]


def test_a_router_failure_falls_back_rather_than_failing_the_turn(monkeypatch):
    def _boom():
        raise RuntimeError("openrouter is down")

    monkeypatch.setattr(cp, "_get_client", _boom)
    profile = _profile(gaming_resolution=None, gaming_fps=None)
    result = asyncio.run(nodes.route(_state(profile)))

    assert result["next_question"] == cp._missing_fields(BuildProfile(**profile))[0]


def test_a_single_missing_field_skips_the_model_entirely(stub_router):
    """Nothing to order means nothing to ask a model about."""
    client = stub_router("1")
    result = asyncio.run(nodes.route(_state(_profile(gaming_fps=None))))

    assert client.calls == 0
    assert result["next_question"] == "target frame rate"


def test_the_chosen_question_is_recorded_for_later_turns(stub_router):
    stub_router("1")
    result = asyncio.run(nodes.route(_state(_profile(gaming_fps=None))))
    # Appended (operator.add on the state field), so it accumulates rather than
    # replacing what earlier turns asked.
    assert result["asked_fields"] == ["target frame rate"]


def test_already_asked_fields_are_shown_to_the_router(stub_router):
    client = stub_router("1")
    state = _state(
        _profile(gaming_resolution=None, gaming_fps=None),
        asked_fields=["target frame rate"],
    )
    asyncio.run(nodes.route(state))

    prompt = client.last_kwargs["messages"][-1]["content"]
    assert "Already asked" in prompt
    assert "target frame rate" in prompt


# ------------------------------------------------------------- profile accretion --


def test_a_field_the_extractor_forgets_this_turn_stays_known():
    """The whole point of checkpointing the profile."""
    previous = _profile()
    current = _profile(gaming_resolution=None, price_sensitivity=None)

    merged = merge_profile(previous, current)

    assert merged["gaming_resolution"] == "1440p"
    assert merged["price_sensitivity"] == "firm"


def test_a_field_the_user_changes_this_turn_is_overwritten():
    """Accretion must not mean the user cannot correct themselves."""
    merged = merge_profile(_profile(), _profile(gaming_resolution="4k"))
    assert merged["gaming_resolution"] == "4k"


def test_unknown_never_overwrites_a_known_use_case_or_tier():
    """'unknown' is a sentinel, not an answer — it must not clobber a real one."""
    merged = merge_profile(
        _profile(), _profile(primary_use="unknown", budget_tier="unknown")
    )
    assert merged["primary_use"] == "gaming"
    assert merged["budget_tier"] == "mid"


def test_list_fields_accumulate_across_turns():
    merged = merge_profile(_profile(games=["Cyberpunk"]), _profile(games=["Factorio"]))
    assert merged["games"] == ["Cyberpunk", "Factorio"]


def test_the_first_turn_has_nothing_to_merge_against():
    current = _profile()
    assert merge_profile(None, current) == current


# ------------------------------------------------------- end to end event stream --


@pytest.fixture
def in_memory_graph(monkeypatch):
    """Force the in-memory checkpointer, as if Valkey were unreachable."""

    async def _no_client():
        return None

    monkeypatch.setattr(graph_mod, "get_client", _no_client)
    graph_mod.reset_for_tests()
    yield
    graph_mod.reset_for_tests()


def _run_turn(messages=None):
    messages = messages or [ChatMessage(role="user", content="gaming pc please")]

    async def scenario():
        return [event async for event in cp.run_chat_turn(messages)]

    return asyncio.run(scenario())


def test_the_elicitation_branch_emits_the_expected_events(
    in_memory_graph, stub_router, monkeypatch
):
    stub_router("1")

    async def _extract(messages, usage_sink=None, session_id=None):
        return BuildProfile(primary_use="gaming", budget_tier="unknown")

    async def _elicit(messages, **kwargs):
        for chunk in ("What ", "resolution?"):
            yield chunk

    monkeypatch.setattr(cp, "extract_profile", _extract)
    monkeypatch.setattr(cp, "stream_elicitation", _elicit)

    events = _run_turn()
    types = [e["type"] for e in events]

    # No "progress" anywhere: the frontend reads any progress event as
    # confirmation the turn is building, so an elicitation turn must not emit one.
    assert "progress" not in types
    assert "build" not in types
    assert types[-2:] == ["usage", "done"]
    assert "".join(e["text"] for e in events if e["type"] == "token") == (
        "What resolution?"
    )


def test_the_build_branch_emits_the_expected_events(
    in_memory_graph, stub_router, monkeypatch
):
    stub_router("1")

    async def _extract(messages, usage_sink=None, session_id=None):
        return BuildProfile(**_profile())

    async def _build(state):
        writer = nodes.get_stream_writer()
        writer({"type": "progress", "step": "resolving", "message": "Building…"})
        writer({"type": "build", "key": "custom_dspy", "data": {"parts": []}})
        return {"build_key": "custom_dspy", "build_data": {"parts": []}}

    async def _recommend(messages, *a, **kw):
        yield "Here you go."

    monkeypatch.setattr(cp, "extract_profile", _extract)
    monkeypatch.setattr(nodes, "build", _build)
    monkeypatch.setattr(cp, "stream_recommendation", _recommend)
    graph_mod.reset_for_tests()  # rebuild with the patched node

    types = [e["type"] for e in _run_turn()]

    assert types == ["progress", "build", "token", "usage", "done"]


def test_a_guest_turn_emits_no_checkpoint_event(
    in_memory_graph, stub_router, monkeypatch
):
    """Guests have no conversation row, so there is nothing to mirror."""
    stub_router("1")

    async def _extract(messages, usage_sink=None, session_id=None):
        return BuildProfile(primary_use="gaming", budget_tier="unknown")

    async def _elicit(messages, **kwargs):
        yield "What resolution?"

    monkeypatch.setattr(cp, "extract_profile", _extract)
    monkeypatch.setattr(cp, "stream_elicitation", _elicit)

    assert "checkpoint" not in [e["type"] for e in _run_turn()]


def test_usage_accumulates_across_every_node(in_memory_graph, stub_router, monkeypatch):
    """Every OpenRouter call in a turn is billed to the conversation.

    Including the router's own. It is a small model asked for one integer, but
    it runs on every elicitation turn, and a per-conversation cost figure that
    quietly omits one of its three calls is worse than no figure at all.
    """
    stub_router("2")  # 10 in / 1 out / $0.0001, on top of the two below

    async def _extract(messages, usage_sink=None, session_id=None):
        if usage_sink is not None:
            usage_sink.update(
                {"tokens_in": 100, "tokens_out": 20, "cost_usd": 0.01, "model": "x"}
            )
        return BuildProfile(primary_use="gaming", budget_tier="unknown")

    async def _elicit(messages, usage_sink=None, **kwargs):
        if usage_sink is not None:
            usage_sink.update(
                {"tokens_in": 50, "tokens_out": 30, "cost_usd": 0.02, "model": "y"}
            )
        yield "What resolution?"

    monkeypatch.setattr(cp, "extract_profile", _extract)
    monkeypatch.setattr(cp, "stream_elicitation", _elicit)

    usage = next(e for e in _run_turn() if e["type"] == "usage")

    assert usage["llm_call_count"] == 3  # extract + route + elicit
    assert usage["tokens_in"] == 100 + 10 + 50
    assert usage["tokens_out"] == 20 + 1 + 30
    assert usage["cost_usd"] == pytest.approx(0.01 + 0.0001 + 0.02)
    assert set(usage["models"]) == {"x", "stub/router", "y"}
