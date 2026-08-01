"""Guards the OpenRouter blocker used by the k6 load tests.

These are cost tests, not feature tests. A regression here does not break a
page — it silently bills real OpenRouter tokens for every virtual user in a
load run, which is exactly the kind of failure nobody notices until the
invoice. Hence asserting the negative cases (no header, wrong secret, secret
unset) as carefully as the positive one.
"""

from __future__ import annotations

import asyncio
from typing import Literal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.loadtest import LoadTestMiddleware, is_load_test

HEADER = "X-Palladium-Load-Test"
SECRET = "test-secret-value"


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setattr(settings, "LOAD_TEST_SECRET", SECRET, raising=False)

    app = FastAPI()
    app.add_middleware(LoadTestMiddleware)

    @app.get("/probe")
    async def probe() -> dict:
        # Read inside the endpoint, not the middleware: this is what proves the
        # ContextVar survives the hop that BaseHTTPMiddleware would have broken.
        return {"stubbed": is_load_test()}

    return TestClient(app)


def test_valid_secret_enables_stub(client: TestClient) -> None:
    assert client.get("/probe", headers={HEADER: SECRET}).json() == {"stubbed": True}


def test_absent_header_uses_real_llm(client: TestClient) -> None:
    assert client.get("/probe").json() == {"stubbed": False}


def test_wrong_secret_uses_real_llm(client: TestClient) -> None:
    assert client.get("/probe", headers={HEADER: "guess"}).json() == {"stubbed": False}


def test_header_ignored_when_secret_unset(monkeypatch) -> None:
    """The default posture: no LOAD_TEST_SECRET means the header does nothing.

    Without this, deploying the feature would leave every environment one
    guessed header name away from serving stubbed recommendations.
    """
    monkeypatch.setattr(settings, "LOAD_TEST_SECRET", "", raising=False)

    app = FastAPI()
    app.add_middleware(LoadTestMiddleware)

    @app.get("/probe")
    async def probe() -> dict:
        return {"stubbed": is_load_test()}

    with TestClient(app) as c:
        assert c.get("/probe", headers={HEADER: "anything"}).json() == {
            "stubbed": False
        }


def test_flag_does_not_leak_between_requests(client: TestClient) -> None:
    """A stubbed request must not taint the next one on a reused task."""
    assert client.get("/probe", headers={HEADER: SECRET}).json() == {"stubbed": True}
    assert client.get("/probe").json() == {"stubbed": False}


def test_stub_client_streams_like_openrouter() -> None:
    """The stub must satisfy the consumer loop in chat_pipeline verbatim."""
    from app.core.loadtest_stubs import StubOpenAIClient
    from app.services.chat_pipeline import _capture_chunk_model, _usage_from_openai

    async def drive() -> tuple[str, dict]:
        client = StubOpenAIClient()
        sink: dict = {}
        stream = await client.chat.completions.create(
            model="stub-model", messages=[], stream=True
        )
        parts = []
        async for chunk in stream:
            _capture_chunk_model(chunk, sink)
            if getattr(chunk, "usage", None):
                sink.update(_usage_from_openai(chunk.usage))
            if not chunk.choices:
                continue
            if delta := chunk.choices[0].delta.content:
                parts.append(delta)
        return "".join(parts), sink

    text, sink = asyncio.run(drive())

    assert text.strip()
    # A stubbed turn is free, and must record as free rather than as unknown —
    # the conversations table sums this column.
    assert sink["cost_usd"] == 0.0
    assert sink["tokens_in"] == 0


def test_stub_lm_satisfies_typed_signatures() -> None:
    """Generic field-type coercion, which is what lets one stub serve all the
    Decide* modules instead of a hand-maintained answer per signature.

    Literal is imported at module scope on purpose: this file uses
    `from __future__ import annotations`, so DSPy resolves the annotation
    strings against module globals — a function-local import would leave it
    unresolvable and the Literal field would silently degrade to str.
    """
    import dspy

    from app.core.loadtest_stubs import make_stub_lm

    class Sig(dspy.Signature):
        question: str = dspy.InputField()
        choice: str = dspy.OutputField()
        score: int = dspy.OutputField()
        price: float = dspy.OutputField()
        tier: Literal["budget", "mid", "high"] = dspy.OutputField()
        tags: list[str] = dspy.OutputField()
        ok: bool = dspy.OutputField()

    with dspy.context(lm=make_stub_lm()):
        r = dspy.Predict(Sig)(question="best gpu?")

    assert isinstance(r.score, int)
    assert isinstance(r.price, float)
    assert isinstance(r.tags, list)
    assert isinstance(r.ok, bool)
    assert r.tier in ("budget", "mid", "high")


def test_branch_fields_produce_a_complete_profile() -> None:
    """primary_use/budget_tier drive which path a /chat turn takes. If the
    generic "stub" placeholder reaches them, is_profile_complete() is False and
    a load test never leaves elicitation — so the expensive eleven-module build
    path, the one most worth load testing, goes unexercised."""
    from app.core.loadtest_stubs import _BRANCH_FIELDS, _placeholder

    assert _placeholder("str", "primary_use") == "gaming"
    assert _placeholder("str", "budget_tier") == "mid"
    # Guard the coupling this relies on: these must remain values that
    # app/schemas/chat.py actually recognises.
    assert set(_BRANCH_FIELDS) >= {"primary_use", "budget_tier"}
