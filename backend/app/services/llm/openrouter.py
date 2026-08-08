"""ChatOpenRouter construction and per-call usage capture.

TWO NUMBERS, AND THEY ARE NOT THE SAME NUMBER.

  tokens   `usage_metadata` on the response. This is what LangSmith reads to
           tally spend, and it is the reason this module exists.
  dollars  OpenRouter's own `cost`, which is what actually gets billed and what
           `conversations.total_cost_usd` has always recorded. LangSmith derives
           its figure from token counts against a pricing table that has no
           entry for models like `google/gemma-4-31b-it`, so its dollar estimate
           is not a substitute.

WHY COST NEEDS A SECOND CALL WHEN STREAMING. langchain-openrouter surfaces
`cost` in `response_metadata` only on the non-streaming path (chat_models.py
:846). When streaming, the usage-only chunk is emitted as
`AIMessageChunk(content="", usage_metadata=...)` and `_create_usage_metadata`
copies token counts alone — cost is dropped before it ever reaches a caller.

So streaming calls read it back from OpenRouter's generation endpoint, keyed by
the generation id the package *does* preserve in `response_metadata["id"]`. That
is OpenRouter's final accounting rather than an inference from token counts, so
it is the more accurate figure, and the request happens after the last token has
already been streamed to the user — it costs latency on the turn's bookkeeping,
not on anything anyone is waiting to read.

The alternative was overriding `_astream` to re-emit cost inline. Rejected: it
means duplicating the package's streaming loop, which would then rot silently on
any upgrade, in exchange for saving one request on a path that has already spent
several seconds talking to a model.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage

from app.core.config import settings

logger = logging.getLogger(__name__)

_GENERATION_URL = "https://openrouter.ai/api/v1/generation"

# OpenRouter finalises a generation's accounting a moment after the stream ends,
# so the first read can 404. Two retries at half a second covers it without
# holding the turn open in the case where it is genuinely never coming.
_COST_FETCH_ATTEMPTS = 3
_COST_FETCH_DELAY_S = 0.5
_COST_FETCH_TIMEOUT_S = 5.0


def get_chat_model(
    model: str,
    *,
    session_id: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    streaming: bool = False,
) -> BaseChatModel:
    """Build a chat model for one call.

    Per-call rather than a cached singleton because `session_id` differs per
    conversation, and it is what groups a turn's calls together in OpenRouter's
    dashboard. Construction is cheap — no connection is opened until the first
    request.
    """
    # Checked before anything else so a load-test request cannot fall through to
    # the real client, and deliberately not cached: the decision is per-request,
    # not per-process. See app/core/loadtest.py.
    from app.core.loadtest import is_load_test

    if is_load_test():
        from app.core.loadtest_stubs import StubChatModel

        return StubChatModel(model_name=model)

    api_key = settings.OPENROUTER_API_KEY
    if not api_key:
        raise OSError("OPENROUTER_API_KEY is not set.")

    from langchain_openrouter import ChatOpenRouter

    return ChatOpenRouter(
        model=model,
        openrouter_api_key=api_key,
        temperature=temperature,
        max_tokens=max_tokens,
        streaming=streaming,
        session_id=session_id,
        # OpenRouter returns `cost` only when the request asks for it. The
        # package sets stream_options.include_usage but never this, so without
        # it there is no cost to read back on any path — including the
        # non-streaming one, where the package would otherwise surface it.
        model_kwargs={"usage": {"include": True}},
    )


def usage_from_message(message: BaseMessage) -> dict[str, Any]:
    """Extract {tokens_in, tokens_out, cost_usd, model, generation_id}.

    Shaped for chat_pipeline._merge_usage, which is unchanged — the turn-usage
    contract into save_turn does not care where the numbers came from.

    cost_usd is None on a streamed response; fetch_generation_cost fills it in
    from generation_id. Returning None rather than 0.0 matters: _merge_usage
    coerces None to 0 for the running total, but a 0.0 here would be
    indistinguishable from a genuinely free call and would hide a broken lookup.
    """
    usage = getattr(message, "usage_metadata", None) or {}
    meta = getattr(message, "response_metadata", None) or {}

    return {
        "tokens_in": usage.get("input_tokens"),
        "tokens_out": usage.get("output_tokens"),
        # Present on non-streaming responses; absent when streamed.
        "cost_usd": meta.get("cost"),
        # The model OpenRouter actually routed to, which may differ from the one
        # requested — that distinction is why this is recorded per call rather
        # than assumed from ChatModelConfig.
        "model": meta.get("model_name"),
        "generation_id": meta.get("id"),
    }


async def fetch_generation_cost(generation_id: str | None) -> float | None:
    """Read a completed generation's real cost from OpenRouter.

    Returns None on any failure, and never raises. A missing cost figure makes
    the conversation's running total an undercount, which is worth a log line;
    it is not worth failing a turn the user has already been served.
    """
    if not generation_id:
        return None

    api_key = settings.OPENROUTER_API_KEY
    if not api_key:
        return None

    import asyncio

    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=_COST_FETCH_TIMEOUT_S) as client:
            for attempt in range(_COST_FETCH_ATTEMPTS):
                response = await client.get(
                    _GENERATION_URL, params={"id": generation_id}, headers=headers
                )
                if response.status_code == 404:
                    # Not finalised yet. Only worth waiting on if there are
                    # attempts left.
                    if attempt + 1 < _COST_FETCH_ATTEMPTS:
                        await asyncio.sleep(_COST_FETCH_DELAY_S)
                        continue
                    break
                response.raise_for_status()
                data = (response.json() or {}).get("data") or {}
                # `total_cost` is the field on this endpoint; `usage` on the
                # completion response carries the same figure under `cost`.
                cost = data.get("total_cost")
                if cost is None:
                    cost = data.get("usage")
                return float(cost) if cost is not None else None
    except Exception:
        logger.warning(
            "could not read cost for generation %s; this turn's cost will be "
            "undercounted",
            generation_id,
            exc_info=True,
        )
        return None

    logger.info(
        "generation %s had no cost available after %d attempts",
        generation_id,
        _COST_FETCH_ATTEMPTS,
    )
    return None
