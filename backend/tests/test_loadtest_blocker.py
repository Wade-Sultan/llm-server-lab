"""Guards the OpenRouter blocker used by the Locust load tests.

These are cost tests, not feature tests. A regression here does not break a
page — it silently bills real OpenRouter tokens for every simulated user in a
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
from app.core.loadtest import LoadTestMiddleware, is_load_test, load_test_scope

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


# --- crossing the Pub/Sub boundary ----------------------------------------
#
# In production /chat does not run the turn; it publishes and relays. Every one
# of the stubbed LM calls therefore happens in the worker process, where the
# middleware's ContextVar does not exist. These three tests cover the whole
# relay: the flag is published, decoded, and re-entered.


def test_chat_publishes_the_load_test_flag(monkeypatch) -> None:
    """The regression that costs money: a payload without `load_test`.

    Nothing about such a turn looks wrong — it streams, it persists, the load
    test reports healthy latency — except that the worker called OpenRouter for
    real, once per build step, for every simulated user.
    """
    from app.api.routes import chat as chat_route

    monkeypatch.setattr(settings, "LOAD_TEST_SECRET", SECRET, raising=False)

    published: list[dict] = []

    async def fake_publish(_turn_id, _conversation_id, payload) -> bool:
        published.append(payload)
        return True

    async def fake_valkey() -> bool:
        return True

    monkeypatch.setattr(chat_route.pubsub, "publish_turn", fake_publish)
    monkeypatch.setattr(chat_route.pubsub, "is_enabled", lambda: True)
    monkeypatch.setattr(chat_route, "valkey_available", fake_valkey)

    app = FastAPI()
    app.add_middleware(LoadTestMiddleware)
    app.include_router(chat_route.router, prefix="/api/v1")

    body = {"messages": [{"role": "user", "content": "hi"}]}
    with TestClient(app) as c:
        # The response body is the relay, which needs a live Valkey; only the
        # published payload matters here, and it is written before any streaming
        # begins.
        c.post("/api/v1/chat", json=body, headers={HEADER: SECRET})
        c.post("/api/v1/chat", json=body)

    assert [p["load_test"] for p in published] == [True, False]


def test_worker_decode_carries_the_flag() -> None:
    import json
    from types import SimpleNamespace

    from app.worker import _decode

    def message(payload: dict) -> SimpleNamespace:
        return SimpleNamespace(data=json.dumps(payload).encode())

    base = {"turn_id": "t1", "messages": [{"role": "user", "content": "hi"}]}

    assert _decode(message({**base, "load_test": True}))[4] is True
    assert _decode(message({**base, "load_test": False}))[4] is False
    # A message published before this field existed must decode as a real turn,
    # not a stubbed one.
    assert _decode(message(base))[4] is False


def test_load_test_scope_refuses_when_secret_unset(monkeypatch) -> None:
    """Safe-by-default, enforced on the worker side too.

    The worker takes the flag from a message body rather than a validated
    header, so the check has to be re-done here — otherwise a replayed or stale
    message could put a worker into stub mode in a deployment where the feature
    is switched off entirely, and real users would get fabricated builds.
    """
    monkeypatch.setattr(settings, "LOAD_TEST_SECRET", SECRET, raising=False)
    with load_test_scope(True):
        assert is_load_test() is True

    monkeypatch.setattr(settings, "LOAD_TEST_SECRET", "", raising=False)
    with load_test_scope(True):
        assert is_load_test() is False

    assert is_load_test() is False


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
