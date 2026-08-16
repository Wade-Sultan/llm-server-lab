"""Demand queue on Valkey for catalog terms nobody has a row for.

WHAT THIS CLOSES. `catalog_match` resolves the titles a user names against the
games / software / ai_models catalogs, and anything it cannot match is dropped
into `unmatched_terms` and forgotten. Those terms are the single best signal we
have about what the catalog is missing — a user asking for "Gemma 4 31B" the
week it ships is telling us exactly what to go find — and until now the only
consequence was a build sized without it.

So an unmatched term is enqueued here, and the discovery sweep drains the most
asked-for ones on its next run. The user who triggered it does not benefit; the
next one does.

A SORTED SET, SCORED BY DEMAND — not a list. With roughly ten searches per sweep
the ordering is the whole design: FIFO would spend that budget on whatever was
typed first, while ZINCRBY spends it on what people keep asking for. Dedupe comes
free with it, which a list would need a separate SET to get.

WRITTEN FROM THE GRAPH, READ BY A JOB. The enqueue happens inside the build
pipeline, i.e. inside a LangGraph node, and is a Valkey write — which is exactly
what that path is allowed to do. Everything that reaches Postgres happens later,
in the sweep. See app/services/telemetry_buffer.py for the same split.

THIS IS UNAUTHENTICATED USER INPUT REACHING A PAID API, eventually. Guards, in
order of how much they matter: a minimum score before a term is eligible (so one
person typing nonsense cannot spend Tavily budget), a length cap and a character
filter on enqueue, a bounded key size, and a TTL so a term nobody has mentioned
in months stops being searched forever.
"""

from __future__ import annotations

import logging

from redis.exceptions import RedisError

from app.core.valkey import get_client

logger = logging.getLogger(__name__)

# One queue per catalog kind, so the ai_model sweep never picks up a game title.
# The kind is known at enqueue time from which answer key the term came out of —
# see dspy_pipeline._resolve_catalog_requirements.
KIND_AI_MODEL = "ai_model"
KIND_GAME = "game"
KIND_SOFTWARE = "software"
_KINDS = frozenset({KIND_AI_MODEL, KIND_GAME, KIND_SOFTWARE})


# Demand scores. `display` holds the last raw spelling seen for a normalized
# key, because the normalized form ("gemma431b") is not what you want to send
# to a search API.
def _queue_key(kind: str) -> str:
    return f"discovery:pending:{kind}"


def _display_key(kind: str) -> str:
    return f"discovery:display:{kind}"


# How many distinct mentions before a term is worth spending a search on. One
# mention is as likely to be a typo as a real gap; two is a pattern.
MIN_SCORE = 2

# High-water / low-water bounds on the sorted set. Eviction is lowest-score
# first, so what survives a flood is what has real demand behind it.
#
# THE GAP BETWEEN THEM IS THE POINT, not slack. Every term enters at score 1 and
# is therefore the coldest thing in the set, so a queue trimmed back to exactly
# its cap evicts the next new term immediately — and the one after that, forever.
# Trimming down to _TRIM_TARGET instead leaves headroom that new terms occupy
# without triggering another trim, which is the window they need to be mentioned
# a second time and earn their place.
#
# The residual limit is honest: a term first mentioned while the set is at the
# high-water mark still dies. With a 2000-term cap, MIN_SCORE filtering and a
# 90-day TTL, being at that mark means 2000 distinct unresolved terms with live
# demand — a flood, and in a flood protecting established demand is correct.
_MAX_TRACKED = 2000
_TRIM_TARGET = 1800

# Terms outside this go nowhere near a search API.
_MAX_TERM_LEN = 80
_MIN_TERM_LEN = 2

# A term nobody has mentioned within this window stops being interesting. Reset
# on every mention, so anything with live demand never expires.
_QUEUE_TTL_S = 90 * 24 * 3600


def _normalize(term: str) -> str:
    """Fold a term to its queue key.

    Reuses catalog_match's normalizer rather than defining a second one: the
    queue has to collapse exactly the spellings the matcher collapsed, or a term
    that failed to match under one folding gets queued under another.
    """
    from app.services.recommender.catalog_match import _normalize_term

    return _normalize_term(term)


def _is_plausible(term: str) -> bool:
    """Cheap junk filter. Not security — the review queue is that — just enough
    to keep obvious garbage from consuming a search."""
    stripped = term.strip()
    if not (_MIN_TERM_LEN <= len(stripped) <= _MAX_TERM_LEN):
        return False
    # Needs at least one letter; "!!!!" and "12345" are not catalog entities.
    return any(c.isalpha() for c in stripped)


async def enqueue(terms: list[str], kind: str) -> int:
    """Record demand for unmatched terms. Returns how many were counted.

    Never raises and never blocks a build: this is a side effect of a build, not
    a part of one.
    """
    if kind not in _KINDS:
        logger.warning("discovery queue: unknown kind %r; ignoring", kind)
        return 0

    candidates = [t for t in (terms or []) if t and _is_plausible(t)]
    if not candidates:
        return 0

    client = await get_client()
    if client is None:
        return 0

    try:
        pipe = client.pipeline()
        for term in candidates:
            key = _normalize(term)
            if not key:
                continue
            pipe.zincrby(_queue_key(kind), 1, key)
            pipe.hset(_display_key(kind), key, term.strip())
        # Refresh the TTL so live demand keeps both keys alive.
        pipe.expire(_queue_key(kind), _QUEUE_TTL_S)
        pipe.expire(_display_key(kind), _QUEUE_TTL_S)
        pipe.zcard(_queue_key(kind))
        results = await pipe.execute()
        await _trim_if_oversized(client, kind, int(results[-1] or 0))
        return len(candidates)
    except (RedisError, OSError):
        logger.warning("discovery queue enqueue failed for %s", kind, exc_info=True)
        return 0


async def _trim_if_oversized(client, kind: str, size: int) -> None:
    """Evict the coldest terms, but ONLY when genuinely over capacity.

    THIS RUNS CONDITIONALLY, AND CUTS BELOW THE CAP, both for the same reason.
    Trimming on every enqueue looks equivalent and is not: every new term enters
    at score 1, so it is always the coldest entry, so a trim that fires at
    capacity evicts each new term the instant it arrives and the queue freezes
    with whatever got in first. Firing only above _MAX_TRACKED and cutting back
    to _TRIM_TARGET leaves headroom that new terms occupy without triggering
    another trim — the window they need to earn a second mention.

    Below the high-water mark nothing is evicted at all, which is the ordinary
    case; the eviction path is for floods, and under a flood protecting
    established demand is the correct policy.

    Both keys are trimmed together — dropping a score while leaving its display
    string would leak the hash without bound, since only the sorted set is
    capped.
    """
    if size <= _MAX_TRACKED:
        return
    excess = size - _TRIM_TARGET
    if excess <= 0:
        return
    victims = await client.zrange(_queue_key(kind), 0, excess - 1)
    if not victims:
        return
    members = [_decode(v) for v in victims]
    pipe = client.pipeline()
    pipe.zrem(_queue_key(kind), *members)
    pipe.hdel(_display_key(kind), *members)
    await pipe.execute()
    logger.info(
        "discovery queue for %s over capacity; evicted %d coldest term(s)",
        kind,
        len(members),
    )


async def take(kind: str, limit: int, *, min_score: int = MIN_SCORE) -> list[str]:
    """The most-requested terms worth searching, highest demand first.

    Returns display spellings, ready to hand to a search API. Does NOT remove
    them — call `resolve` once a term has actually been dealt with, so a sweep
    that dies halfway leaves its queue intact.
    """
    if kind not in _KINDS or limit <= 0:
        return []

    client = await get_client()
    if client is None:
        return []

    try:
        scored = await client.zrevrange(_queue_key(kind), 0, limit - 1, withscores=True)
    except (RedisError, OSError):
        logger.warning("discovery queue read failed for %s", kind, exc_info=True)
        return []

    keys = [_decode(member) for member, score in scored if score >= min_score]
    if not keys:
        return []

    try:
        displays = await client.hmget(_display_key(kind), keys)
    except (RedisError, OSError):
        logger.warning("discovery queue display read failed", exc_info=True)
        displays = []

    # Fall back to the normalized key if the display mapping has expired out
    # from under the score — a worse search query, but better than skipping it.
    out: list[str] = []
    for idx, key in enumerate(keys):
        display = _decode(displays[idx]) if idx < len(displays) else None
        out.append(display or key)
    return out


async def resolve(terms: list[str], kind: str) -> int:
    """Drop terms from the queue once they've been searched or added.

    Call after a sweep has processed a term, and when a model is approved into
    the catalog — otherwise an entity we now HAVE keeps burning searches.
    """
    if kind not in _KINDS or not terms:
        return 0

    client = await get_client()
    if client is None:
        return 0

    keys = [k for k in (_normalize(t) for t in terms) if k]
    if not keys:
        return 0

    try:
        pipe = client.pipeline()
        pipe.zrem(_queue_key(kind), *keys)
        pipe.hdel(_display_key(kind), *keys)
        results = await pipe.execute()
        return int(results[0] or 0)
    except (RedisError, OSError):
        logger.warning("discovery queue resolve failed for %s", kind, exc_info=True)
        return 0


async def pending_count(kind: str, *, min_score: int = MIN_SCORE) -> int | None:
    """How many terms are eligible for the next sweep. None if unavailable."""
    if kind not in _KINDS:
        return None
    client = await get_client()
    if client is None:
        return None
    try:
        return int(await client.zcount(_queue_key(kind), min_score, "+inf"))
    except (RedisError, OSError):
        logger.warning("discovery queue count failed for %s", kind, exc_info=True)
        return None


def _decode(value) -> str:
    """redis-py hands back bytes or str depending on decode_responses."""
    if value is None:
        return ""
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)
