"""Guards the hand-written LangGraph checkpointer on Valkey.

Its whole reason for existing is that langgraph-checkpoint-redis cannot run on
Memorystore (no RedisJSON, and Google's FT.* is a vector engine that will not do
the standalone metadata lookups that package needs). So there is no upstream test
suite covering this behaviour — these are it.

What matters here is the same thing that matters in test_turn_stream.py: the
failure modes are silent. A checkpointer that quietly returns None does not break
a page, it just makes the assistant re-ask a question the user already answered,
which reads as the model being forgetful rather than as a bug. So the negative
cases carry most of the weight.

Backed by a fake rather than a real Valkey, for the reason given at the top of
test_turn_stream.py.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.graph import checkpoint as ckpt_mod
from app.services.graph.checkpoint import AsyncValkeySaver


class FakeValkey:
    """Enough redis-py surface for the checkpointer: KV, ZSET, HASH, pipelines."""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.zsets: dict[str, dict[str, float]] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.expiries: dict[str, int] = {}

    async def set(self, key, value, ex=None):
        self.kv[key] = value
        if ex:
            self.expiries[key] = ex
        return True

    async def get(self, key):
        return self.kv.get(key)

    async def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(mapping)
        return len(mapping)

    async def zrevrangebylex(self, key, max_, min_):
        return sorted(self.zsets.get(key, {}), reverse=True)

    async def hset(self, key, mapping=None):
        self.hashes.setdefault(key, {}).update(mapping or {})
        return len(mapping or {})

    async def hkeys(self, key):
        return list(self.hashes.get(key, {}))

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    async def expire(self, key, ttl):
        self.expiries[key] = ttl
        return True

    async def delete(self, *keys):
        count = 0
        for key in keys:
            for store in (self.kv, self.zsets, self.hashes):
                if store.pop(key, None) is not None:
                    count += 1
        return count

    async def scan_iter(self, match=None, count=None):
        prefix = (match or "*").rstrip("*")
        seen = set()
        for store in (self.kv, self.zsets, self.hashes):
            for key in list(store):
                if key.startswith(prefix) and key not in seen:
                    seen.add(key)
                    yield key

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

    monkeypatch.setattr(ckpt_mod, "get_client", _get_client)
    return client


@pytest.fixture
def no_valkey(monkeypatch):
    async def _get_client():
        return None

    monkeypatch.setattr(ckpt_mod, "get_client", _get_client)


def _config(thread_id="11111111-1111-1111-1111-111111111111", checkpoint_id=None):
    configurable = {"thread_id": thread_id, "checkpoint_ns": ""}
    if checkpoint_id:
        configurable["checkpoint_id"] = checkpoint_id
    return {"configurable": configurable}


def _checkpoint(checkpoint_id: str, **values):
    return {
        "v": 1,
        "id": checkpoint_id,
        "ts": "2026-08-07T00:00:00+00:00",
        "channel_values": dict(values),
        "channel_versions": {k: 1 for k in values},
        "versions_seen": {},
    }


# ------------------------------------------------------------------ round trip --


def test_put_then_get_returns_the_same_channel_values(fake):
    saver = AsyncValkeySaver()

    async def scenario():
        await saver.aput(
            _config(),
            _checkpoint("cp-001", profile={"primary_use": "gaming"}),
            {"step": 1},
            {},
        )
        return await saver.aget_tuple(_config())

    tuple_ = asyncio.run(scenario())
    assert tuple_ is not None
    assert tuple_.checkpoint["channel_values"]["profile"] == {"primary_use": "gaming"}
    assert tuple_.metadata["step"] == 1


def test_get_without_an_id_returns_the_newest_checkpoint(fake):
    """Latest-wins is what a new turn relies on to resume a conversation."""
    saver = AsyncValkeySaver()

    async def scenario():
        for cid, use in (("cp-001", "gaming"), ("cp-002", "server")):
            await saver.aput(_config(), _checkpoint(cid, profile=use), {}, {})
        return await saver.aget_tuple(_config())

    tuple_ = asyncio.run(scenario())
    assert tuple_.checkpoint["id"] == "cp-002"
    assert tuple_.checkpoint["channel_values"]["profile"] == "server"


def test_get_with_an_explicit_id_returns_that_checkpoint(fake):
    saver = AsyncValkeySaver()

    async def scenario():
        for cid in ("cp-001", "cp-002"):
            await saver.aput(_config(), _checkpoint(cid, n=cid), {}, {})
        return await saver.aget_tuple(_config(checkpoint_id="cp-001"))

    assert asyncio.run(scenario()).checkpoint["id"] == "cp-001"


def test_parent_config_records_the_chain(fake):
    saver = AsyncValkeySaver()

    async def scenario():
        await saver.aput(_config(), _checkpoint("cp-001"), {}, {})
        await saver.aput(_config(checkpoint_id="cp-001"), _checkpoint("cp-002"), {}, {})
        return await saver.aget_tuple(_config(checkpoint_id="cp-002"))

    tuple_ = asyncio.run(scenario())
    assert tuple_.parent_config["configurable"]["checkpoint_id"] == "cp-001"


# ---------------------------------------------------------------- expiry / TTL --


def test_every_key_written_carries_a_ttl(fake):
    """Without this a conversation that never commits leaks keys forever."""
    saver = AsyncValkeySaver(ttl_s=1234)

    async def scenario():
        await saver.aput(_config(), _checkpoint("cp-001"), {}, {})
        await saver.aput_writes(
            _config(checkpoint_id="cp-001"), [("ch", "v")], "task-1"
        )

    asyncio.run(scenario())
    assert fake.expiries, "no TTL was set on anything"
    assert set(fake.expiries.values()) == {1234}


def test_index_entry_without_its_checkpoint_reads_as_absent(fake):
    """The index outlives an individual checkpoint whose TTL was not refreshed.

    That must read as "nothing to resume", not as an error and not as a
    half-built tuple.
    """
    saver = AsyncValkeySaver()

    async def scenario():
        await saver.aput(_config(), _checkpoint("cp-001"), {}, {})
        fake.kv.clear()  # checkpoint expired; the ZSET entry remains
        return await saver.aget_tuple(_config())

    assert asyncio.run(scenario()) is None


def test_a_checkpoint_in_an_unknown_format_is_discarded_not_misread(fake):
    saver = AsyncValkeySaver()

    async def scenario():
        await saver.aput(_config(), _checkpoint("cp-001"), {}, {})
        key = next(iter(fake.kv))
        fake.kv[key] = fake.kv[key].replace('"v": 1', '"v": 99')
        return await saver.aget_tuple(_config())

    assert asyncio.run(scenario()) is None


# -------------------------------------------------------------- pending writes --


def test_pending_writes_come_back_with_their_checkpoint(fake):
    saver = AsyncValkeySaver()

    async def scenario():
        await saver.aput(_config(), _checkpoint("cp-001"), {}, {})
        await saver.aput_writes(
            _config(checkpoint_id="cp-001"),
            [("messages", "hello"), ("profile", {"budget_tier": "mid"})],
            "task-1",
        )
        return await saver.aget_tuple(_config())

    pending = asyncio.run(scenario()).pending_writes
    assert [(c, v) for _tid, c, v in pending] == [
        ("messages", "hello"),
        ("profile", {"budget_tier": "mid"}),
    ]


def test_replaying_a_task_does_not_duplicate_its_writes(fake):
    """A redelivered Pub/Sub message re-runs a task. It must be idempotent."""
    saver = AsyncValkeySaver()

    async def scenario():
        await saver.aput(_config(), _checkpoint("cp-001"), {}, {})
        for _ in range(3):
            await saver.aput_writes(
                _config(checkpoint_id="cp-001"), [("messages", "hello")], "task-1"
            )
        return await saver.aget_tuple(_config())

    assert len(asyncio.run(scenario()).pending_writes) == 1


# ---------------------------------------------------------------------- listing --


def test_list_returns_newest_first_and_respects_limit(fake):
    saver = AsyncValkeySaver()

    async def scenario():
        for cid in ("cp-001", "cp-002", "cp-003"):
            await saver.aput(_config(), _checkpoint(cid), {}, {})
        return [t.checkpoint["id"] async for t in saver.alist(_config(), limit=2)]

    assert asyncio.run(scenario()) == ["cp-003", "cp-002"]


def test_list_before_excludes_the_boundary(fake):
    saver = AsyncValkeySaver()

    async def scenario():
        for cid in ("cp-001", "cp-002", "cp-003"):
            await saver.aput(_config(), _checkpoint(cid), {}, {})
        return [
            t.checkpoint["id"]
            async for t in saver.alist(
                _config(), before=_config(checkpoint_id="cp-003")
            )
        ]

    assert asyncio.run(scenario()) == ["cp-002", "cp-001"]


def test_delete_thread_removes_checkpoints_and_writes(fake):
    saver = AsyncValkeySaver()

    async def scenario():
        await saver.aput(_config(), _checkpoint("cp-001"), {}, {})
        await saver.aput_writes(
            _config(checkpoint_id="cp-001"), [("ch", "v")], "task-1"
        )
        await saver.adelete_thread("11111111-1111-1111-1111-111111111111")
        return await saver.aget_tuple(_config())

    assert asyncio.run(scenario()) is None
    assert not fake.kv and not fake.hashes


# --------------------------------------------------------------- valkey is down --


def test_reads_return_none_rather_than_raising_when_valkey_is_down(
    no_valkey, monkeypatch
):
    """Losing resumability must not cost the user their build."""
    saver = AsyncValkeySaver()

    # A guest thread id, so the Postgres hydration path is not consulted —
    # that path is exercised separately below.
    assert asyncio.run(saver.aget_tuple(_config(thread_id="turn:abc"))) is None


def test_writes_are_dropped_silently_when_valkey_is_down(no_valkey):
    saver = AsyncValkeySaver()

    async def scenario():
        returned = await saver.aput(_config(), _checkpoint("cp-001"), {}, {})
        await saver.aput_writes(
            _config(checkpoint_id="cp-001"), [("ch", "v")], "task-1"
        )
        return returned

    # Still returns a usable config: the graph keeps running, it just will not
    # be resumable.
    assert asyncio.run(scenario())["configurable"]["checkpoint_id"] == "cp-001"


def test_a_valkey_error_mid_read_does_not_propagate(fake, monkeypatch):
    from redis.exceptions import RedisError

    saver = AsyncValkeySaver()

    async def scenario():
        await saver.aput(_config(), _checkpoint("cp-001"), {}, {})

        async def _boom(*a, **kw):
            raise RedisError("connection reset")

        monkeypatch.setattr(fake, "zrevrangebylex", _boom)
        # A guest id so it cannot fall through to Postgres.
        return await saver.aget_tuple(_config(thread_id="turn:abc"))

    assert asyncio.run(scenario()) is None


# -------------------------------------------------------------- postgres mirror --


def test_guest_threads_never_reach_postgres(no_valkey, monkeypatch):
    """Guests have no Conversation row; looking one up every turn is waste."""
    saver = AsyncValkeySaver()
    called = False

    async def _hydrate(*a, **kw):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(saver, "_hydrate_from_postgres", _hydrate)
    asyncio.run(saver.aget_tuple(_config(thread_id="turn:abcdef")))
    assert called is False


def test_conversation_threads_fall_through_to_postgres(no_valkey, monkeypatch):
    saver = AsyncValkeySaver()
    called = False

    async def _hydrate(*a, **kw):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(saver, "_hydrate_from_postgres", _hydrate)
    asyncio.run(saver.aget_tuple(_config()))
    assert called is True


def test_an_explicit_checkpoint_id_does_not_fall_through_to_postgres(
    no_valkey, monkeypatch
):
    """The mirror holds only the latest, so a historical id has nothing to find."""
    saver = AsyncValkeySaver()
    called = False

    async def _hydrate(*a, **kw):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(saver, "_hydrate_from_postgres", _hydrate)
    asyncio.run(saver.aget_tuple(_config(checkpoint_id="cp-001")))
    assert called is False


def test_mirror_round_trips_through_the_json_envelope(fake):
    """to_mirror's output must survive a JSONB column and come back intact."""
    import json

    saver = AsyncValkeySaver()

    async def scenario():
        await saver.aput(
            _config(), _checkpoint("cp-001", profile={"budget_tier": "high"}), {}, {}
        )
        return await saver.aget_tuple(_config())

    mirror = saver.to_mirror(asyncio.run(scenario()))
    revived = json.loads(json.dumps(mirror))  # what Postgres does to it
    assert revived["ns"] == ""
    checkpoint = saver._decode(revived["cp"])
    assert checkpoint["channel_values"]["profile"] == {"budget_tier": "high"}
