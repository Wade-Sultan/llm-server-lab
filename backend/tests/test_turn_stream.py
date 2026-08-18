"""Guards the Valkey-backed turn stream and the evict-on-commit buffer.

These are durability tests. A regression here does not break a page — the stream
still renders, the build still appears — it silently makes turns non-recoverable
again, which only shows up as users reporting a build that "disappeared" after
their phone locked. So the negative cases (Valkey down, commit failed, duplicate
delivery) matter more than the happy path here.

Backed by a fake rather than a real Valkey: the behaviours under test are this
code's decisions about *when* to write, evict and stop, none of which depend on
redis-py's actual network layer. A real instance would only add a service
dependency to `pytest`.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.core import valkey
from app.core.config import settings
from app.services import chat_buffer, turn_stream


class FakeValkey:
    """Enough of the redis-py surface for streams, SET/GET/DEL and pipelines."""

    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict]]] = {}
        self.kv: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.expiries: dict[str, int] = {}
        self._seq = 0

    # -- streams -------------------------------------------------------
    async def xadd(self, key, fields, maxlen=None, approximate=True):
        self._seq += 1
        entry_id = f"1-{self._seq}"
        self.streams.setdefault(key, []).append((entry_id, dict(fields)))
        if maxlen is not None:
            self.streams[key] = self.streams[key][-maxlen:]
        return entry_id

    async def xread(self, streams, count=None, block=None):
        out = []
        for key, last in streams.items():
            entries = self.streams.get(key, [])
            # "0" means from the beginning; otherwise strictly after `last`.
            newer = [e for e in entries if e[0] > last] if last != "0" else entries
            if count:
                newer = newer[:count]
            if newer:
                out.append((key, newer))
        return out

    # -- kv ------------------------------------------------------------
    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        if ex:
            self.expiries[key] = ex
        return True

    async def get(self, key):
        return self.kv.get(key)

    async def delete(self, *keys):
        return sum(1 for k in keys if self.kv.pop(k, None) is not None)

    async def exists(self, key):
        return 1 if (key in self.streams or key in self.kv or key in self.lists) else 0

    async def expire(self, key, ttl):
        self.expiries[key] = ttl
        return True

    # -- lists ---------------------------------------------------------
    async def rpush(self, key, *values):
        self.lists.setdefault(key, []).extend(str(v) for v in values)
        return len(self.lists[key])

    async def lrem(self, key, count, value):
        items = self.lists.get(key, [])
        assert count == 0, "the wake queue relies on count=0 (remove every match)"
        removed = items.count(str(value))
        self.lists[key] = [i for i in items if i != str(value)]
        return removed

    async def llen(self, key):
        return len(self.lists.get(key, []))

    async def scan_iter(self, match=None, count=None):
        prefix = (match or "*").rstrip("*")
        for key in list(self.kv):
            if key.startswith(prefix):
                yield key

    async def ping(self):
        return True

    def pipeline(self):
        return _FakePipeline(self)


class _FakePipeline:
    """Queues calls and replays them on execute(), like redis-py's pipeline."""

    def __init__(self, client: FakeValkey) -> None:
        self._client = client
        self._queued: list[tuple[str, tuple, dict]] = []

    def xadd(self, *a, **kw):
        self._queued.append(("xadd", a, kw))
        return self

    def rpush(self, *a, **kw):
        self._queued.append(("rpush", a, kw))
        return self

    def expire(self, *a, **kw):
        self._queued.append(("expire", a, kw))
        return self

    async def execute(self):
        return [
            await getattr(self._client, name)(*a, **kw) for name, a, kw in self._queued
        ]


@pytest.fixture
def fake(monkeypatch) -> FakeValkey:
    client = FakeValkey()

    async def _get_client():
        return client

    monkeypatch.setattr(valkey, "get_client", _get_client)
    monkeypatch.setattr(turn_stream, "get_client", _get_client)
    monkeypatch.setattr(chat_buffer, "get_client", _get_client)
    # tail() reads through the BLOCKING client, which is a separate pool with a
    # socket timeout longer than its own XREAD block (app/core/valkey.py). The
    # fake stands in for both — the split is about socket timeouts, which a fake
    # has none of — but it has to be patched explicitly or tail() sees None and
    # returns as if Valkey were unreachable.
    monkeypatch.setattr(valkey, "get_blocking_client", _get_client)
    monkeypatch.setattr(turn_stream, "get_blocking_client", _get_client)
    return client


@pytest.fixture
def no_valkey(monkeypatch):
    """Valkey unreachable — every accessor returns None."""

    async def _get_client():
        return None

    monkeypatch.setattr(turn_stream, "get_client", _get_client)
    monkeypatch.setattr(turn_stream, "get_blocking_client", _get_client)
    monkeypatch.setattr(chat_buffer, "get_client", _get_client)


# ---------------------------------------------------------------- streaming --


@pytest.mark.usefixtures("fake")
def test_tail_replays_events_written_before_the_reader_attached():
    """The whole reason this is a stream and not pub/sub.

    The browser attaches after POST /chat returns, so anything the worker emitted
    in between must still be there. With pub/sub semantics this test is
    impossible to pass.
    """

    async def scenario():
        await turn_stream.emit("t1", {"type": "progress", "step": "cpu"})
        await turn_stream.emit("t1", {"type": "token", "text": "Hello"})
        await turn_stream.emit_end("t1")

        seen = [item async for item in turn_stream.tail("t1") if item is not None]
        return [e for _, e in seen]

    events = asyncio.run(scenario())
    assert events == [
        {"type": "progress", "step": "cpu"},
        {"type": "token", "text": "Hello"},
    ]


@pytest.mark.usefixtures("fake")
def test_tail_resumes_after_last_id_without_duplicating():
    """A reconnect must not re-render text the user already has on screen."""

    async def scenario():
        await turn_stream.emit("t2", {"type": "token", "text": "one"})
        await turn_stream.emit("t2", {"type": "token", "text": "two"})
        await turn_stream.emit_end("t2")

        first = [i async for i in turn_stream.tail("t2") if i is not None]
        # Reconnect from the id of the first event, as the frontend would.
        resume_from = first[0][0]
        second = [
            i
            async for i in turn_stream.tail("t2", last_id=resume_from)
            if i is not None
        ]
        return [e["text"] for _, e in second]

    assert asyncio.run(scenario()) == ["two"]


@pytest.mark.usefixtures("fake")
def test_tail_stops_at_terminal_entry_not_at_pipeline_done():
    """`done` is emitted before persistence; only `end` means it is safe to leave.

    If the tail stopped on `done`, a client could disconnect while the turn was
    still committing — and the buffer eviction that follows would then run under
    a reader that had not finished.
    """

    async def scenario():
        await turn_stream.emit("t3", {"type": "done"})
        await turn_stream.emit("t3", {"type": "token", "text": "after-done"})
        await turn_stream.emit_end("t3")
        return [e async for e in turn_stream.tail("t3") if e is not None]

    events = [e for _, e in asyncio.run(scenario())]
    assert {"type": "token", "text": "after-done"} in events


@pytest.mark.usefixtures("fake")
def test_tail_gives_up_rather_than_blocking_forever():
    """A worker OOM-killed mid-turn never writes `end`.

    Without the idle bound the reader would hold an HTTP connection and a Valkey
    connection open indefinitely waiting for an entry that is never coming.
    """

    async def scenario():
        await turn_stream.emit("t4", {"type": "token", "text": "partial"})
        # No emit_end. Tight bounds so the test does not actually wait 10 minutes.
        return [
            i
            async for i in turn_stream.tail("t4", idle_timeout_ms=1, max_idle_rounds=3)
        ]

    items = asyncio.run(scenario())
    # The one real event, then idle heartbeats, then a clean return.
    assert items[0] is not None
    assert items.count(None) == 3


@pytest.mark.usefixtures("no_valkey")
def test_emit_survives_valkey_being_down():
    """A failed emit must never kill the pipeline that produced the event."""

    async def scenario():
        emitted = await turn_stream.emit("t5", {"type": "token"})
        # An unavailable Valkey ends the tail immediately rather than hanging;
        # the route turns that into a clean [DONE] instead of a stuck response.
        tailed = [i async for i in turn_stream.tail("t5")]
        return emitted, tailed

    emitted, tailed = asyncio.run(scenario())
    assert emitted is False
    assert tailed == []


def test_maxlen_bounds_a_pathological_turn(fake, monkeypatch):
    monkeypatch.setattr(settings, "TURN_STREAM_MAXLEN", 5, raising=False)

    async def scenario():
        for i in range(20):
            await turn_stream.emit("t6", {"type": "token", "text": str(i)})

    asyncio.run(scenario())
    assert len(fake.streams[turn_stream.stream_key("t6")]) == 5


# ------------------------------------------------------------------- claims --


@pytest.mark.usefixtures("fake")
def test_claim_is_exclusive_so_redelivery_does_not_rerun_a_turn():
    """Pub/Sub is at-least-once; without this the user sees duplicated text
    and the turn is billed to OpenRouter twice."""

    async def scenario():
        first = await turn_stream.claim("t7", "worker-a", 60)
        second = await turn_stream.claim("t7", "worker-b", 60)
        return first, second

    first, second = asyncio.run(scenario())
    assert first is True
    assert second is False


@pytest.mark.usefixtures("fake")
def test_released_claim_allows_a_retry():
    """A turn cancelled by SIGTERM must be re-runnable, or a rolling restart
    silently drops every turn that was in flight."""

    async def scenario():
        await turn_stream.claim("t8", "worker-a", 60)
        await turn_stream.release_claim("t8")
        return await turn_stream.claim("t8", "worker-b", 60)

    assert asyncio.run(scenario()) is True


@pytest.mark.usefixtures("no_valkey")
def test_claim_succeeds_when_valkey_is_down():
    """Running unclaimed beats refusing to run the turn at all."""
    assert asyncio.run(turn_stream.claim("t9", "worker-a", 60)) is True


# ------------------------------------------------------------------- buffer --


@pytest.mark.usefixtures("fake")
def test_buffer_round_trips():
    async def scenario():
        await chat_buffer.save("conv-1", {"assistant_text": "hi", "turn_usage": None})
        return await chat_buffer.load("conv-1")

    assert asyncio.run(scenario())["assistant_text"] == "hi"


@pytest.mark.usefixtures("fake")
def test_discard_removes_the_buffer():
    async def scenario():
        await chat_buffer.save("conv-2", {"assistant_text": "x"})
        removed = await chat_buffer.discard("conv-2")
        return removed, await chat_buffer.load("conv-2")

    removed, after = asyncio.run(scenario())
    assert removed is True
    assert after is None


def test_corrupt_buffer_is_dropped_rather_than_returned(fake):
    """A half-written buffer replayed into Postgres is worse than no buffer."""
    fake.kv[chat_buffer.buffer_key("conv-3")] = "{not json"

    async def scenario():
        loaded = await chat_buffer.load("conv-3")
        return loaded, await chat_buffer.load("conv-3")

    first, second = asyncio.run(scenario())
    assert first is None and second is None
    assert chat_buffer.buffer_key("conv-3") not in fake.kv


def test_buffer_carries_a_ttl_so_a_dead_worker_cannot_leak_it(fake):
    asyncio.run(chat_buffer.save("conv-4", {"assistant_text": "x"}))
    assert fake.expiries[chat_buffer.buffer_key("conv-4")] == settings.CHAT_BUFFER_TTL_S


@pytest.mark.usefixtures("fake")
def test_buffer_and_stream_share_a_hash_tag():
    """Cluster mode: a conversation's buffer and its stream must land on one
    shard, or any future multi-key operation fails with CROSSSLOT."""
    assert "{conv-5}" in chat_buffer.buffer_key("conv-5")
    assert "{conv-5}" in turn_stream.stream_key("conv-5")


# ----------------------------------------------------------- evict-on-commit --


def test_buffer_is_evicted_only_on_a_confirmed_commit(fake, monkeypatch):
    """The core durability guarantee.

    save_turn swallows its own exceptions and returns normally on failure, so
    "the function returned" says nothing about whether rows were written. Evicting
    on completion rather than on the commit flag would discard the only remaining
    copy of a turn the user already paid for.
    """
    from app.schemas.chat import ChatMessage
    from app.services import turn_runner

    async def fake_pipeline(_messages, **_kwargs):
        yield {"type": "token", "text": "hello"}
        yield {"type": "done"}

    monkeypatch.setattr(turn_runner, "run_chat_turn", fake_pipeline)
    monkeypatch.setattr(turn_runner, "save_turn", lambda *a, **kw: False)

    asyncio.run(
        turn_runner.run_turn(
            "t10",
            [ChatMessage(role="user", content="hi")],
            {"uid": "u1", "email": "u@example.com"},
            "conv-6",
        )
    )

    raw = fake.kv.get(chat_buffer.buffer_key("conv-6"))
    assert raw is not None, "buffer must survive a failed commit"
    assert json.loads(raw)["assistant_text"] == "hello"


def test_buffer_is_evicted_when_the_commit_succeeds(fake, monkeypatch):
    from app.schemas.chat import ChatMessage
    from app.services import turn_runner

    async def fake_pipeline(_messages, **_kwargs):
        yield {"type": "token", "text": "hello"}

    monkeypatch.setattr(turn_runner, "run_chat_turn", fake_pipeline)
    monkeypatch.setattr(turn_runner, "save_turn", lambda *a, **kw: True)

    asyncio.run(
        turn_runner.run_turn(
            "t11",
            [ChatMessage(role="user", content="hi")],
            {"uid": "u1", "email": "u@example.com"},
            "conv-7",
        )
    )

    assert chat_buffer.buffer_key("conv-7") not in fake.kv


def test_internal_events_never_reach_the_stream(fake, monkeypatch):
    """`usage` carries OpenRouter spend. Forwarding it would leak cost data to
    the browser, which is the one thing the original _event_stream was careful
    about and the easiest thing to lose in a refactor."""
    from app.schemas.chat import ChatMessage
    from app.services import turn_runner

    async def fake_pipeline(_messages, **_kwargs):
        yield {"type": "token", "text": "hi"}
        yield {"type": "usage", "cost_usd": 0.42}
        yield {"type": "reference_estimate", "key": "k", "data": {}}
        yield {"type": "done"}

    monkeypatch.setattr(turn_runner, "run_chat_turn", fake_pipeline)
    monkeypatch.setattr(turn_runner, "save_turn", lambda *a, **kw: True)

    asyncio.run(
        turn_runner.run_turn(
            "t12", [ChatMessage(role="user", content="hi")], None, None
        )
    )

    emitted = [
        json.loads(f["e"])["type"]
        for _, f in fake.streams[turn_stream.stream_key("t12")]
    ]
    assert "usage" not in emitted
    assert "reference_estimate" not in emitted
    assert emitted == ["token", "done", "end"]


def test_guest_turns_are_streamed_but_never_buffered(fake, monkeypatch):
    from app.schemas.chat import ChatMessage
    from app.services import turn_runner

    async def fake_pipeline(_messages, **_kwargs):
        yield {"type": "token", "text": "hi"}

    monkeypatch.setattr(turn_runner, "run_chat_turn", fake_pipeline)

    asyncio.run(
        turn_runner.run_turn(
            "t13", [ChatMessage(role="user", content="hi")], None, None
        )
    )

    assert fake.streams[turn_stream.stream_key("t13")]
    assert not [k for k in fake.kv if k.startswith("chat:buf:")]


def test_pipeline_error_still_terminates_the_stream(fake, monkeypatch):
    """A reader blocked on a stream that never ends is a hung browser tab —
    a worse failure than a visible error."""
    from app.schemas.chat import ChatMessage
    from app.services import turn_runner

    async def exploding_pipeline(_messages, **_kwargs):
        yield {"type": "token", "text": "partial"}
        raise RuntimeError("OpenRouter exploded")

    monkeypatch.setattr(turn_runner, "run_chat_turn", exploding_pipeline)

    asyncio.run(
        turn_runner.run_turn(
            "t14", [ChatMessage(role="user", content="hi")], None, None
        )
    )

    emitted = [
        json.loads(f["e"])["type"]
        for _, f in fake.streams[turn_stream.stream_key("t14")]
    ]
    assert emitted[-1] == "end"


def test_cancellation_leaves_the_stream_open_for_redelivery(fake, monkeypatch):
    """SIGTERM mid-turn must NOT write a terminal entry: the Pub/Sub message goes
    un-acked and redelivers, and a reader that reconnects should keep waiting for
    the retry rather than being told the turn ended."""
    from app.schemas.chat import ChatMessage
    from app.services import turn_runner

    async def cancelled_pipeline(_messages, **_kwargs):
        yield {"type": "token", "text": "partial"}
        raise asyncio.CancelledError()

    monkeypatch.setattr(turn_runner, "run_chat_turn", cancelled_pipeline)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            turn_runner.run_turn(
                "t15", [ChatMessage(role="user", content="hi")], None, None
            )
        )

    emitted = [
        json.loads(f["e"])["type"]
        for _, f in fake.streams[turn_stream.stream_key("t15")]
    ]
    assert "end" not in emitted


# ------------------------------------------------------------------ metrics --


@pytest.mark.usefixtures("fake")
def test_retained_count_reflects_unpersisted_turns():
    """Backs the `palladium_chat_buffers_retained` gauge and the alert policy in
    deploy/monitoring/. Steady state is 0; anything else is turns the user paid
    for that are not in Postgres."""

    async def scenario():
        assert await chat_buffer.count_retained() == 0
        await chat_buffer.save("conv-a", {"assistant_text": "x"})
        await chat_buffer.save("conv-b", {"assistant_text": "y"})
        during = await chat_buffer.count_retained()
        await chat_buffer.discard("conv-a")
        await chat_buffer.discard("conv-b")
        return during, await chat_buffer.count_retained()

    during, after = asyncio.run(scenario())
    assert during == 2
    assert after == 0


@pytest.mark.usefixtures("no_valkey")
def test_retained_count_is_none_rather_than_zero_when_valkey_is_down():
    """The distinction the alert depends on. Reporting 0 for an unreachable
    Valkey would set the gauge to a healthy value and suppress the alert at
    exactly the moment persistence is least observable."""
    assert asyncio.run(chat_buffer.count_retained()) is None


def test_commit_outcome_is_counted(fake, monkeypatch):
    from app.core.turn_metrics import TURN_COMMITS
    from app.schemas.chat import ChatMessage
    from app.services import turn_runner

    async def fake_pipeline(_messages, **_kwargs):
        yield {"type": "token", "text": "hi"}

    monkeypatch.setattr(turn_runner, "run_chat_turn", fake_pipeline)
    monkeypatch.setattr(turn_runner, "save_turn", lambda *a, **kw: False)

    before = TURN_COMMITS.labels(result="failed")._value.get()
    asyncio.run(
        turn_runner.run_turn(
            "t20",
            [ChatMessage(role="user", content="hi")],
            {"uid": "u1", "email": "u@example.com"},
            "conv-m",
        )
    )
    assert TURN_COMMITS.labels(result="failed")._value.get() == before + 1
    assert fake.kv.get(chat_buffer.buffer_key("conv-m")) is not None


@pytest.mark.usefixtures("fake")
def test_inflight_gauge_returns_to_zero_even_when_a_turn_is_cancelled(monkeypatch):
    """A leaked increment looks exactly like a permanently saturated worker."""
    from app.core.turn_metrics import TURNS_INFLIGHT
    from app.schemas.chat import ChatMessage
    from app.services import turn_runner

    async def cancelled_pipeline(_messages, **_kwargs):
        yield {"type": "token", "text": "partial"}
        raise asyncio.CancelledError()

    monkeypatch.setattr(turn_runner, "run_chat_turn", cancelled_pipeline)

    before = TURNS_INFLIGHT._value.get()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            turn_runner.run_turn(
                "t21", [ChatMessage(role="user", content="hi")], None, None
            )
        )
    assert TURNS_INFLIGHT._value.get() == before


# ------------------------------------------------- blocking-read contract --
#
# The bug these cover cost a full outage and was invisible to every other test
# in this file, because a fake client has no socket and therefore no socket
# timeout. tail() issues XREAD BLOCK 15000; redis-py enforces the pool's
# socket_timeout on that parked read like any other, so reading through the
# ordinary 5s client aborted the block, retried, and finally raised — which
# stream_turn_into reports as "That build didn't start."
#
# It stayed hidden while the worker pool had a warm floor: an event always
# arrived inside 5s, so the block never ran long enough to trip. The first cold
# start after scale-to-zero broke every /chat.


def test_tail_block_stays_under_the_blocking_clients_socket_timeout():
    """The two numbers live in different modules and must not drift apart."""
    import inspect

    from app.core.valkey import BLOCKING_READ_TIMEOUT_S

    default_block_ms = (
        inspect.signature(turn_stream.tail).parameters["idle_timeout_ms"].default
    )
    assert default_block_ms / 1000 < BLOCKING_READ_TIMEOUT_S, (
        f"tail() blocks for {default_block_ms}ms but its client aborts reads at "
        f"{BLOCKING_READ_TIMEOUT_S}s — every read would fail before the block elapsed"
    )


@pytest.mark.usefixtures("fake")
def test_tail_refuses_a_block_longer_than_its_socket_timeout():
    """Fail loudly at the call, not silently as an apparently empty stream."""
    from app.core.valkey import BLOCKING_READ_TIMEOUT_S

    async def scenario():
        return [
            i
            async for i in turn_stream.tail(
                "t22", idle_timeout_ms=int(BLOCKING_READ_TIMEOUT_S * 1000) + 1
            )
        ]

    with pytest.raises(ValueError, match="socket timeout"):
        asyncio.run(scenario())


def test_the_two_clients_really_do_differ_in_socket_timeout(monkeypatch):
    """Guards the reason the second pool exists at all.

    Collapsing them back into one client is the regression this catches: it
    would look harmless in every test above and break only on a cold worker.
    """
    from app.core import valkey as valkey_module

    monkeypatch.setattr(valkey_module.settings, "VALKEY_HOST", "localhost")
    monkeypatch.setattr(valkey_module.settings, "VALKEY_CLUSTER", False)
    monkeypatch.setattr(valkey_module.settings, "VALKEY_TLS", False)
    valkey_module.reset_for_tests()

    captured: dict[float, object] = {}

    class _StubClient:
        def __init__(self, **kwargs):
            captured[kwargs["socket_timeout"]] = self

        async def ping(self):
            return True

    monkeypatch.setattr(valkey_module, "Redis", _StubClient)

    async def scenario():
        await valkey_module.get_client()
        await valkey_module.get_blocking_client()

    asyncio.run(scenario())
    valkey_module.reset_for_tests()

    assert len(captured) == 2, "both clients were built with the same socket timeout"
    ordinary, blocking = sorted(captured)
    assert blocking > turn_stream.tail.__defaults__[1] / 1000 > ordinary


# -------------------------------------------------------------- wake queue --
#
# The fast half of the worker pool's scale-from-zero. KEDA polls LLEN on this
# list (deploy/overlays/prod/keda-worker.yaml); everything below is really an
# assertion about what that scaler will see.


@pytest.mark.usefixtures("fake")
def test_a_dispatched_turn_makes_the_pool_look_needed():
    async def scenario():
        before = await turn_stream.wake_depth()
        await turn_stream.push_wake("t30")
        return before, await turn_stream.wake_depth()

    before, after = asyncio.run(scenario())
    assert before == 0, "an idle pool must read 0 or KEDA never scales down"
    assert after == 1, "one queued turn must be visible to the scaler immediately"


@pytest.mark.usefixtures("fake")
def test_pickup_clears_the_turn_so_the_pool_can_scale_back_down():
    """Cleared at pickup, not completion — the pod already exists by then."""

    async def scenario():
        await turn_stream.push_wake("t31")
        await turn_stream.clear_wake("t31")
        return await turn_stream.wake_depth()

    assert asyncio.run(scenario()) == 0


@pytest.mark.usefixtures("fake")
def test_a_redelivered_turn_cannot_leave_a_straggler_holding_a_pod_up():
    """Pub/Sub is at-least-once, so the same id can be pushed more than once.

    LREM with count=0 removes every match. With count=1 the survivor would keep
    the scaler active forever, which is a pod that never scales down.
    """

    async def scenario():
        await turn_stream.push_wake("t32")
        await turn_stream.push_wake("t32")
        await turn_stream.clear_wake("t32")
        return await turn_stream.wake_depth()

    assert asyncio.run(scenario()) == 0


@pytest.mark.usefixtures("fake")
def test_an_undrained_queue_expires_rather_than_pinning_a_pod_forever(fake):
    """The one leak this design allows, and its backstop.

    A turn that no worker ever receives keeps the pool awake — correct, but
    unbounded without a TTL, which would quietly undo scale-to-zero.
    """

    async def scenario():
        await turn_stream.push_wake("t33")

    asyncio.run(scenario())
    assert fake.expiries[turn_stream.WAKE_KEY] == settings.TURN_STREAM_TTL_S


@pytest.mark.usefixtures("no_valkey")
def test_the_wake_queue_degrades_instead_of_failing_the_turn():
    """No Valkey means no fast wake-up, not a failed /chat.

    The turn is already on Pub/Sub at this point; losing this only costs the
    slower Cloud Monitoring trigger, which is what the system had before.
    """

    async def scenario():
        pushed = await turn_stream.push_wake("t34")
        await turn_stream.clear_wake("t34")  # must not raise either
        return pushed, await turn_stream.wake_depth()

    pushed, depth = asyncio.run(scenario())
    assert pushed is False
    assert depth is None


@pytest.mark.usefixtures("fake")
def test_the_worker_clears_the_wake_entry_even_for_a_duplicate_delivery():
    """_handle clears before the claim, so a losing duplicate still clears.

    Clearing after the claim would mean a redelivery that loses the claim race
    returns with the entry still in the list, asking KEDA for a pod to handle a
    turn another pod is already running.
    """
    from app import worker as worker_module
    from app.schemas.chat import ChatMessage

    async def scenario():
        await turn_stream.push_wake("t35")
        # Somebody else already owns it.
        await turn_stream.claim("t35", "another-worker", 60)
        await worker_module._handle(
            "t35", [ChatMessage(role="user", content="hi")], None, None
        )
        return await turn_stream.wake_depth()

    assert asyncio.run(scenario()) == 0
