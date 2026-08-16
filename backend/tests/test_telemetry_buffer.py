"""Build telemetry goes to Valkey first, Postgres second — never from the graph.

THE INVARIANT UNDER TEST. The LangGraph turn reads from Postgres but writes only
to Valkey; persistence belongs to save_turn, which runs after the graph stream
has drained. `BuildRecorder` used to be the sole exception — it opened a session
inside the `build` node and committed there. These tests pin the fix, including
the one that matters most: that `finish()` reaches no database at all.

Backed by a fake rather than a real Valkey, same as test_valkey_checkpointer.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.models.build_session import BuildSessionStatus
from app.services import telemetry_buffer as tb
from app.services.recommender import recording as rec


class FakeValkey:
    """Enough redis-py surface for a list-backed buffer."""

    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}
        self.fail = False

    async def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    async def ltrim(self, key, start, end):
        items = self.lists.get(key, [])
        # Mirror Redis semantics closely enough for the cases exercised here.
        self.lists[key] = items[start:] if end == -1 else items[start : end + 1]
        return True

    async def lrange(self, key, start, end):
        items = self.lists.get(key, [])
        return items[start:] if end == -1 else items[start : end + 1]

    async def llen(self, key):
        return len(self.lists.get(key, []))

    def pipeline(self, transaction=True):
        return _FakePipeline(self)


class _FakePipeline:
    def __init__(self, client: FakeValkey) -> None:
        self._client = client
        self._queued: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name):
        def _queue(*a, **kw):
            self._queued.append((name, a, kw))
            return self

        return _queue

    async def execute(self):
        return [
            await getattr(self._client, name)(*a, **kw) for name, a, kw in self._queued
        ]


@pytest.fixture
def fake(monkeypatch) -> FakeValkey:
    client = FakeValkey()

    async def _get_client():
        return client

    monkeypatch.setattr(tb, "get_client", _get_client)
    return client


@pytest.fixture
def no_valkey(monkeypatch):
    async def _get_client():
        return None

    monkeypatch.setattr(tb, "get_client", _get_client)


# --- The buffer ---------------------------------------------------------------


def test_push_then_peek_round_trips(fake):
    async def scenario():
        await tb.push({"session_id": "abc", "decisions": []})
        return await tb.peek(10)

    assert asyncio.run(scenario())[0]["session_id"] == "abc"


def test_peek_does_not_remove(fake):
    """Entries leave only on ack, after Postgres confirms — same discipline as
    chat_buffer's evict-on-commit."""

    async def scenario():
        await tb.push({"session_id": "abc"})
        await tb.peek(10)
        return await tb.count_pending()

    assert asyncio.run(scenario()) == 1


def test_ack_removes_exactly_what_was_read(fake):
    async def scenario():
        for i in range(3):
            await tb.push({"session_id": str(i)})
        await tb.ack(2)
        return await tb.peek(10)

    remaining = asyncio.run(scenario())
    assert [p["session_id"] for p in remaining] == ["2"]


def test_ack_by_read_count_not_parsed_count(fake):
    """A poison entry must not wedge the head of the queue behind good ones."""

    async def scenario():
        fake.lists[tb.PENDING_KEY] = ["{not json", json.dumps({"session_id": "ok"})]
        payloads = await tb.peek(10)
        # One parsed, two read.
        await tb.ack(2)
        return payloads, await tb.count_pending()

    payloads, remaining = asyncio.run(scenario())
    assert [p["session_id"] for p in payloads] == ["ok"]
    assert remaining == 0


def test_push_degrades_when_valkey_is_down(no_valkey):
    """Telemetry is lost rather than written from the graph. That is the trade
    the invariant asks for, and it must not raise."""
    assert asyncio.run(tb.push({"session_id": "abc"})) is False


def test_count_pending_is_none_when_valkey_is_down(no_valkey):
    assert asyncio.run(tb.count_pending()) is None


def test_buffer_is_length_capped(fake, monkeypatch):
    """A permanently broken drain must not grow the key without bound."""
    monkeypatch.setattr(tb, "_MAX_PENDING", 3)

    async def scenario():
        for i in range(6):
            await tb.push({"session_id": str(i)})
        return await tb.peek(10)

    kept = [p["session_id"] for p in asyncio.run(scenario())]
    # Newest survive: telemetry ages into irrelevance.
    assert kept == ["3", "4", "5"]


# --- The invariant ------------------------------------------------------------


def _recorder() -> rec.BuildRecorder:
    request = SimpleNamespace(
        model_dump=lambda: {"use_cases": ["gaming"]}, budget_usd=1500
    )
    return rec.BuildRecorder(request, "test-version")


def test_finish_never_opens_a_database_session(fake, monkeypatch):
    """THE REGRESSION. finish() runs inside the `build` graph node; if it can
    reach Postgres at all, the invariant is not enforced — it is merely being
    observed. Any DB access here should fail loudly."""

    def _explode(*a, **kw):
        raise AssertionError("BuildRecorder.finish touched the database")

    monkeypatch.setattr("app.core.db.AsyncSessionLocal", _explode)

    async def scenario():
        recorder = _recorder()
        recorder.finish(BuildSessionStatus.COMPLETED)
        # finish() schedules a task; let it run.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return await tb.peek(10)

    buffered = asyncio.run(scenario())
    assert len(buffered) == 1
    assert buffered[0]["status"] == "completed"


def test_finish_buffers_the_whole_run(fake):
    async def scenario():
        recorder = _recorder()
        recorder.record_deterministic_decision(
            category="gpu",
            sequence_order=6,
            signature_name="DecideGPU",
            signature_version=3,
            candidates_json='[{"chipset": "RTX 5070 Ti"}]',
            input_state={"budget_total": 1500},
            output_decision={"gpu_chipset": "RTX 5070 Ti"},
            chosen_name="RTX 5070 Ti",
            latency_ms=4,
        )
        recorder.finish(BuildSessionStatus.COMPLETED)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return await tb.peek(10)

    payload = asyncio.run(scenario())[0]
    assert payload["pipeline_version"] == "test-version"
    assert len(payload["decisions"]) == 1
    assert payload["decisions"][0]["chosen_name"] == "RTX 5070 Ti"


# --- Chronology ---------------------------------------------------------------
# module_decisions.created_at is part of ix_module_decisions_category_pipeline_created,
# the index every GEPA extraction windows on. If the buffer let it default to
# now(), a run drained hours later by the backstop job would land in the wrong
# cohort — a silent data-quality bug rather than a visible failure.


def test_the_payload_carries_build_time_not_drain_time(fake):
    async def scenario():
        recorder = _recorder()
        recorder.record_deterministic_decision(
            category="cpu",
            sequence_order=1,
            signature_name="DecideCPU",
            signature_version=1,
            candidates_json="[]",
            input_state={},
            output_decision={},
            chosen_name="7800X3D",
            latency_ms=1,
        )
        recorder.finish(BuildSessionStatus.COMPLETED)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return await tb.peek(10)

    payload = asyncio.run(scenario())[0]
    assert payload["started_at"]
    assert payload["finished_at"]
    assert payload["decisions"][0]["recorded_at"]


def test_timestamps_survive_the_json_round_trip(fake):
    """They cross Valkey as ISO strings and have to come back as aware datetimes,
    or a timestamptz column reads them as server-local time."""

    async def scenario():
        recorder = _recorder()
        recorder.finish(BuildSessionStatus.COMPLETED)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return await tb.peek(10)

    payload = asyncio.run(scenario())[0]
    parsed = rec._as_datetime(payload["started_at"])
    assert parsed is not None
    assert parsed.tzinfo is not None


def test_a_naive_timestamp_is_read_as_utc():
    parsed = rec._as_datetime("2026-08-16T10:30:00")
    assert parsed is not None and parsed.tzinfo is not None


def test_an_unparseable_timestamp_defers_to_the_column_default():
    assert rec._as_datetime("not a timestamp") is None
    assert rec._as_datetime(None) is None


def test_a_lost_status_records_error_not_success():
    """Recording an unknown status as completed would put a lie in the training
    data; a run whose status did not survive is a run that went wrong."""
    assert rec._as_status("nonsense") is BuildSessionStatus.ERROR
    assert rec._as_status("completed") is BuildSessionStatus.COMPLETED


def test_cost_survives_as_a_number_not_a_string():
    """total_cost_usd is Numeric; _payload stringifies Decimal to clear JSON, so
    the drain has to turn it back."""
    assert rec._as_decimal("0.0125") is not None
    assert float(rec._as_decimal("0.0125")) == pytest.approx(0.0125)
    assert rec._as_decimal(None) is None
    assert rec._as_decimal("garbage") is None
