"""Guards the two-number usage capture in app/services/llm/.

THE FAILURE THIS EXISTS TO CATCH IS SILENT. langchain-openrouter surfaces
OpenRouter's real `cost` on non-streaming responses but drops it when streaming
(chat_models.py emits the usage chunk with token counts alone). Two of the three
chat call sites stream, so if the generation-id lookup ever stops working,
nothing errors — `conversations.total_cost_usd` simply stops growing, and the
first sign is a billing figure that disagrees with OpenRouter's dashboard weeks
later.

So the assertions below are mostly about cost being *absent* in the right places
and *recovered* in the right places, rather than about any happy path.
"""

from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import AIMessage

from app.services.chat_pipeline import _finalize_usage, _merge_usage, _stream_text
from app.services.graph.state import new_usage
from app.services.llm import openrouter, usage_from_message


class _FakeStreamModel:
    """Streams two text chunks then a usage-only chunk, as ChatOpenRouter does."""

    def __init__(self, *, cost=None, generation_id="gen-123"):
        self.cost = cost
        self.generation_id = generation_id

    async def astream(self, messages, **kwargs):
        from langchain_core.messages import AIMessageChunk

        yield AIMessageChunk(content="Hello ")
        yield AIMessageChunk(content="world")

        meta = {"model_name": "stub/model", "id": self.generation_id}
        if self.cost is not None:
            meta["cost"] = self.cost
        yield AIMessageChunk(
            content="",
            usage_metadata={
                "input_tokens": 11,
                "output_tokens": 7,
                "total_tokens": 18,
            },
            response_metadata=meta,
        )


def _drive(model) -> tuple[str, dict]:
    async def scenario():
        sink: dict = {}
        parts = [chunk async for chunk in _stream_text(model, [], sink)]
        return "".join(parts), sink

    return asyncio.run(scenario())


# ------------------------------------------------------------------- mapping --


def test_tokens_and_model_survive_a_stream():
    text, sink = _drive(_FakeStreamModel())

    assert text == "Hello world"
    assert sink["tokens_in"] == 11
    assert sink["tokens_out"] == 7
    # The model OpenRouter actually routed to, not the one requested.
    assert sink["model"] == "stub/model"


def test_a_streamed_call_reports_no_cost_of_its_own():
    """The precondition for the whole lookup. If this ever starts returning a
    cost, the extra request in _finalize_usage has become dead weight."""
    _, sink = _drive(_FakeStreamModel())
    assert sink["cost_usd"] is None
    assert sink["generation_id"] == "gen-123"


def test_a_non_streaming_response_carries_its_own_cost():
    message = AIMessage(
        content="2",
        usage_metadata={"input_tokens": 4, "output_tokens": 1, "total_tokens": 5},
        response_metadata={"cost": 0.00042, "model_name": "stub/router", "id": "g-1"},
    )
    usage = usage_from_message(message)

    assert usage["cost_usd"] == 0.00042
    assert usage["tokens_in"] == 4


# ------------------------------------------------------------------ finalize --


def test_finalize_looks_up_cost_when_the_stream_did_not_supply_it(monkeypatch):
    async def _fetch(generation_id):
        assert generation_id == "gen-123"
        return 0.0031

    monkeypatch.setattr("app.services.chat_pipeline.fetch_generation_cost", _fetch)

    _, sink = _drive(_FakeStreamModel())
    asyncio.run(_finalize_usage(sink))

    assert sink["cost_usd"] == 0.0031


def test_finalize_makes_no_request_when_cost_is_already_known(monkeypatch):
    """The router's non-streaming call must not pay for a lookup it doesn't need."""
    called = False

    async def _fetch(generation_id):
        nonlocal called
        called = True
        return 0.0

    monkeypatch.setattr("app.services.chat_pipeline.fetch_generation_cost", _fetch)

    sink = {"cost_usd": 0.5, "generation_id": "gen-123"}
    asyncio.run(_finalize_usage(sink))

    assert called is False
    assert sink["cost_usd"] == 0.5


def test_a_failed_lookup_undercounts_rather_than_failing_the_turn(monkeypatch):
    """The user has already been served; a missing cost is not worth an error."""

    async def _fetch(generation_id):
        return None

    monkeypatch.setattr("app.services.chat_pipeline.fetch_generation_cost", _fetch)

    _, sink = _drive(_FakeStreamModel())
    asyncio.run(_finalize_usage(sink))

    total = new_usage()
    _merge_usage(total, sink)

    assert sink["cost_usd"] is None
    # None coerces to 0 in the running total rather than raising.
    assert total["cost_usd"] == 0.0
    assert total["tokens_in"] == 11


def test_no_generation_id_means_no_request(monkeypatch):
    """A stubbed (load-test) call has no id, and must not hit the network."""
    assert asyncio.run(openrouter.fetch_generation_cost(None)) is None


# ------------------------------------------------------- the running total --


def test_a_turns_calls_accumulate_into_one_total():
    total = new_usage()
    for cost in (0.001, 0.002, 0.0005):
        _merge_usage(
            total,
            {
                "tokens_in": 10,
                "tokens_out": 5,
                "cost_usd": cost,
                "model": "stub/model",
            },
        )

    assert total["llm_call_count"] == 3
    assert total["tokens_in"] == 30
    assert total["cost_usd"] == pytest.approx(0.0035)
    # Deduplicated: one model served all three calls.
    assert total["models"] == ["stub/model"]
