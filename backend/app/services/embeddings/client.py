"""
client.py
=========
Thin async wrapper over the OpenAI embeddings endpoint.

Deliberately not routed through OpenRouter: OpenRouter proxies chat completions
and exposes no embeddings endpoint, so this is the one LLM-adjacent call in the
codebase that talks to a provider directly. That also means it does not appear
in the OpenRouter cost telemetry the recommender relies on — embedding spend is
tracked by row count instead (see EmbeddingResult.total_tokens).

DEGRADES TO None, NEVER RAISES UPWARD. Every caller of this module treats an
absent embedding as "no semantic match available", which is the same state the
system is in before the first backfill runs. Making an embedding failure fatal
would take down the build pipeline over an optional enrichment.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from openai import AsyncOpenAI, OpenAIError

from app.core.config import settings

logger = logging.getLogger(__name__)

# The endpoint accepts up to 2048 inputs per call, but the request body is also
# capped and long part descriptions add up fast. 128 keeps a batch comfortably
# inside the body limit while still amortizing round-trip latency across the
# backfill.
_BATCH_SIZE = 128

# Embedding calls are idempotent and cheap to retry, and the failure mode that
# matters (a 429 mid-backfill) resolves on its own.
_MAX_RETRIES = 3
_RETRY_BASE_DELAY_S = 1.0


@dataclass
class EmbeddingResult:
    """Vectors in the same order as the input texts, plus what they cost.

    `vectors` is index-aligned with the request: a None slot means that one text
    failed while others in its batch succeeded, so callers must not assume a
    dense list.
    """

    vectors: list[list[float] | None] = field(default_factory=list)
    total_tokens: int = 0
    model: str = ""


_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI | None:
    """Lazily build the client so importing this module never requires a key."""
    global _client
    if _client is not None:
        return _client
    if not settings.OPENAI_API_KEY:
        return None
    _client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


def is_configured() -> bool:
    """Whether embedding calls can be made at all.

    Checked by the reconcile sweep before it does any database work, so an
    unconfigured environment logs one line instead of walking the whole catalog
    to produce nothing.
    """
    return bool(settings.OPENAI_API_KEY)


async def _embed_batch(texts: list[str]) -> tuple[list[list[float] | None], int]:
    """One API call, with retries. Returns (vectors, tokens)."""
    client = _get_client()
    if client is None:
        return [None] * len(texts), 0

    for attempt in range(_MAX_RETRIES):
        try:
            response = await client.embeddings.create(
                model=settings.EMBEDDING_MODEL,
                input=texts,
                dimensions=settings.EMBEDDING_DIMS,
            )
            # The API documents data as input-ordered, but it carries an
            # explicit index and reordering here is free — relying on the
            # documented order would fail silently and unrepeatably.
            vectors: list[list[float] | None] = [None] * len(texts)
            for item in response.data:
                if 0 <= item.index < len(vectors):
                    vectors[item.index] = item.embedding
            return vectors, (response.usage.total_tokens if response.usage else 0)
        except OpenAIError as exc:
            if attempt == _MAX_RETRIES - 1:
                logger.warning(
                    "embedding batch of %d failed after %d attempts: %s",
                    len(texts),
                    _MAX_RETRIES,
                    exc,
                )
                return [None] * len(texts), 0
            await asyncio.sleep(_RETRY_BASE_DELAY_S * (2**attempt))

    return [None] * len(texts), 0


async def embed_texts(texts: list[str]) -> EmbeddingResult:
    """Embed a list of texts, batching as needed.

    Batches run sequentially rather than concurrently. The backfill is not
    latency-sensitive, and firing dozens of parallel requests is the reliable
    way to earn a rate limit that then costs more time than the concurrency
    saved.
    """
    if not texts:
        return EmbeddingResult(model=settings.EMBEDDING_MODEL)
    if not is_configured():
        logger.warning(
            "OPENAI_API_KEY is unset — skipping %d embedding(s). Semantic "
            "matching stays disabled until it is configured.",
            len(texts),
        )
        return EmbeddingResult(
            vectors=[None] * len(texts), model=settings.EMBEDDING_MODEL
        )

    result = EmbeddingResult(model=settings.EMBEDDING_MODEL)
    for start in range(0, len(texts), _BATCH_SIZE):
        batch = texts[start : start + _BATCH_SIZE]
        vectors, tokens = await _embed_batch(batch)
        result.vectors.extend(vectors)
        result.total_tokens += tokens
    return result


async def embed_one(text: str) -> list[float] | None:
    """Embed a single text — the query path. None if unavailable."""
    if not text or not text.strip():
        return None
    result = await embed_texts([text])
    return result.vectors[0] if result.vectors else None
