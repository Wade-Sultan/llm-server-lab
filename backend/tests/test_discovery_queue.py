"""Demand queue for catalog terms the matcher could not resolve.

The case that motivated it: someone asks for a model released last week, the
catalog has no row, the build is sized without it, and nothing records that we
were asked. Now the term is counted, and the next sweep goes looking.

The ordering is the design. With roughly ten searches per sweep, spending them
on the most-asked-for terms rather than the earliest-typed is the entire value —
so most of these tests are about the score, not the plumbing.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.discovery import queue


class FakeValkey:
    """Enough redis-py surface for a ZSET + HASH queue."""

    def __init__(self) -> None:
        self.zsets: dict[str, dict[str, float]] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.expiries: dict[str, int] = {}

    async def zincrby(self, key, amount, member):
        z = self.zsets.setdefault(key, {})
        z[member] = z.get(member, 0) + amount
        return z[member]

    async def hset(self, key, field=None, value=None):
        self.hashes.setdefault(key, {})[field] = value
        return 1

    async def zcard(self, key):
        return len(self.zsets.get(key, {}))

    async def zrange(self, key, start, end):
        z = self.zsets.get(key, {})
        ordered = [m for m, _ in sorted(z.items(), key=lambda kv: (kv[1], kv[0]))]
        return ordered[start:] if end == -1 else ordered[start : end + 1]

    async def expire(self, key, ttl):
        self.expiries[key] = ttl
        return True

    async def zrevrange(self, key, start, end, withscores=False):
        z = self.zsets.get(key, {})
        ordered = sorted(z.items(), key=lambda kv: (-kv[1], kv[0]))
        window = ordered[start:] if end == -1 else ordered[start : end + 1]
        return window if withscores else [m for m, _ in window]

    async def hmget(self, key, fields):
        h = self.hashes.get(key, {})
        return [h.get(f) for f in fields]

    async def zrem(self, key, *members):
        z = self.zsets.get(key, {})
        return sum(1 for m in members if z.pop(m, None) is not None)

    async def hdel(self, key, *fields):
        h = self.hashes.get(key, {})
        return sum(1 for f in fields if h.pop(f, None) is not None)

    async def zcount(self, key, low, high):
        return sum(1 for s in self.zsets.get(key, {}).values() if s >= low)

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

    monkeypatch.setattr(queue, "get_client", _get_client)
    return client


@pytest.fixture
def no_valkey(monkeypatch):
    async def _get_client():
        return None

    monkeypatch.setattr(queue, "get_client", _get_client)


def _mention(term: str, times: int = 1, kind: str = queue.KIND_AI_MODEL):
    async def scenario():
        for _ in range(times):
            await queue.enqueue([term], kind)

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Demand ordering
# ---------------------------------------------------------------------------


def test_the_most_requested_term_comes_first(fake):
    """The whole point: ten searches per sweep go to what people keep asking
    for, not to whatever was typed first."""
    _mention("Llama 5 8B", times=2)
    _mention("Gemma 4 31B", times=9)
    _mention("Mistral Next", times=5)

    top = asyncio.run(queue.take(queue.KIND_AI_MODEL, 3))
    assert top[0] == "Gemma 4 31B"
    assert top[1] == "Mistral Next"


def test_a_single_mention_is_not_worth_a_search(fake):
    """One mention is as likely to be a typo as a real gap."""
    _mention("Gemma 4 31B", times=1)
    assert asyncio.run(queue.take(queue.KIND_AI_MODEL, 10)) == []

    _mention("Gemma 4 31B", times=1)
    assert asyncio.run(queue.take(queue.KIND_AI_MODEL, 10)) == ["Gemma 4 31B"]


def test_take_respects_the_limit(fake):
    for name in ("A model", "B model", "C model", "D model"):
        _mention(name, times=3)
    assert len(asyncio.run(queue.take(queue.KIND_AI_MODEL, 2))) == 2


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def test_spellings_collapse_to_one_entry(fake):
    """Must fold exactly the way the matcher folds, or a term that failed to
    match under one spelling gets queued under another."""
    _mention("Gemma 4 31B")
    _mention("gemma-4-31b")
    _mention("GEMMA 4 31B")

    top = asyncio.run(queue.take(queue.KIND_AI_MODEL, 10))
    assert len(top) == 1


def test_the_display_spelling_is_what_gets_searched(fake):
    """The normalized key ("gemma431b") is a terrible search query."""
    _mention("gemma-4-31b")
    _mention("Gemma 4 31B")
    assert asyncio.run(queue.take(queue.KIND_AI_MODEL, 10)) == ["Gemma 4 31B"]


# ---------------------------------------------------------------------------
# Guards — this is unauthenticated input that eventually reaches a paid API
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("junk", ["", " ", "!", "!!!!!", "12345", "x" * 200])
def test_junk_never_enters_the_queue(fake, junk):
    asyncio.run(queue.enqueue([junk], queue.KIND_AI_MODEL))
    assert asyncio.run(queue.take(queue.KIND_AI_MODEL, 10, min_score=1)) == []


def test_nothing_is_evicted_below_the_high_water_mark(fake, monkeypatch):
    """The ordinary case. Eviction is a flood response, not routine maintenance."""
    monkeypatch.setattr(queue, "_MAX_TRACKED", 10)
    monkeypatch.setattr(queue, "_TRIM_TARGET", 8)
    for i in range(9):
        _mention(f"model {i}", times=2)
    assert len(asyncio.run(queue.take(queue.KIND_AI_MODEL, 50))) == 9


def test_a_trim_leaves_headroom_for_new_terms(fake, monkeypatch):
    """THE BUG THIS PINS. Cutting back to exactly the cap evicts each new term
    the instant it arrives — it enters at score 1 and is always the coldest — so
    the queue freezes with whatever got in first and never learns anything new.
    Cutting below the cap is what gives a newcomer a window to earn a second
    mention."""
    monkeypatch.setattr(queue, "_MAX_TRACKED", 10)
    monkeypatch.setattr(queue, "_TRIM_TARGET", 6)

    # Push past the high-water mark to force at least one trim.
    for i in range(12):
        _mention(f"model {i}", times=3)
    size = len(fake.zsets[queue._queue_key(queue.KIND_AI_MODEL)])
    assert size <= queue._MAX_TRACKED  # the cap is actually enforced
    assert size < 12  # and something really was evicted

    # Headroom below the cap now exists, so a newcomer survives to accumulate.
    _mention("newcomer", times=2)
    assert "newcomer" in asyncio.run(queue.take(queue.KIND_AI_MODEL, 50))


def test_a_flood_evicts_the_coldest_not_the_established(fake, monkeypatch):
    """Over capacity, score-order eviction protects real demand — which is
    exactly what a flood is trying to displace."""
    monkeypatch.setattr(queue, "_MAX_TRACKED", 8)
    monkeypatch.setattr(queue, "_TRIM_TARGET", 5)
    _mention("real demand", times=20)
    for i in range(40):
        _mention(f"flood {i}", times=1)

    survivors = asyncio.run(queue.take(queue.KIND_AI_MODEL, 50, min_score=1))
    assert "real demand" in survivors
    assert len(survivors) <= queue._MAX_TRACKED


def test_trimming_does_not_leak_the_display_hash(fake, monkeypatch):
    """Only the sorted set is capped, so an evicted score whose display string
    stayed behind would grow the hash without bound."""
    monkeypatch.setattr(queue, "_MAX_TRACKED", 6)
    monkeypatch.setattr(queue, "_TRIM_TARGET", 3)
    _mention("keeper", times=20)
    for i in range(20):
        _mention(f"cold {i}", times=1)

    zset = fake.zsets[queue._queue_key(queue.KIND_AI_MODEL)]
    hash_ = fake.hashes[queue._display_key(queue.KIND_AI_MODEL)]
    assert set(hash_) == set(zset)


def test_entries_carry_a_ttl(fake):
    _mention("Gemma 4 31B")
    assert fake.expiries[queue._queue_key(queue.KIND_AI_MODEL)] == queue._QUEUE_TTL_S


def test_an_unknown_kind_is_refused(fake):
    assert asyncio.run(queue.enqueue(["something"], "not_a_kind")) == 0


def test_everything_degrades_when_valkey_is_down(no_valkey):
    assert asyncio.run(queue.enqueue(["Gemma 4 31B"], queue.KIND_AI_MODEL)) == 0
    assert asyncio.run(queue.take(queue.KIND_AI_MODEL, 10)) == []
    assert asyncio.run(queue.resolve(["Gemma 4 31B"], queue.KIND_AI_MODEL)) == 0
    assert asyncio.run(queue.pending_count(queue.KIND_AI_MODEL)) is None


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_take_does_not_consume(fake):
    """A sweep that dies halfway must leave its queue intact."""
    _mention("Gemma 4 31B", times=3)
    asyncio.run(queue.take(queue.KIND_AI_MODEL, 10))
    assert asyncio.run(queue.take(queue.KIND_AI_MODEL, 10)) == ["Gemma 4 31B"]


def test_resolve_removes_a_handled_term(fake):
    """Otherwise an entity we now HAVE keeps burning searches forever."""
    _mention("Gemma 4 31B", times=3)
    assert asyncio.run(queue.resolve(["Gemma 4 31B"], queue.KIND_AI_MODEL)) == 1
    assert asyncio.run(queue.take(queue.KIND_AI_MODEL, 10)) == []


def test_resolve_matches_on_any_spelling(fake):
    _mention("Gemma 4 31B", times=3)
    assert asyncio.run(queue.resolve(["gemma-4-31b"], queue.KIND_AI_MODEL)) == 1


def test_kinds_do_not_bleed_into_each_other(fake):
    """The ai_model sweep must never pick up a game title."""
    _mention("Gemma 4 31B", times=3, kind=queue.KIND_AI_MODEL)
    _mention("Arc Raiders", times=3, kind=queue.KIND_GAME)

    assert asyncio.run(queue.take(queue.KIND_AI_MODEL, 10)) == ["Gemma 4 31B"]
    assert asyncio.run(queue.take(queue.KIND_GAME, 10)) == ["Arc Raiders"]


def test_pending_count_only_counts_eligible_terms(fake):
    _mention("below threshold", times=1)
    _mention("above threshold", times=4)
    assert asyncio.run(queue.pending_count(queue.KIND_AI_MODEL)) == 1
