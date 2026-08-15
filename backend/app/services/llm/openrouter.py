"""ChatOpenRouter construction and per-call usage capture.

TWO NUMBERS, AND THEY ARE NOT THE SAME NUMBER.

  tokens   `usage_metadata` on the response. This is what LangSmith reads to
           tally spend, and it is the reason this module exists.
  dollars  OpenRouter's own `cost`, which is what actually gets billed and what
           `conversations.total_cost_usd` has always recorded. LangSmith derives
           its figure from token counts against a pricing table that has no
           entry for models like `google/gemma-4-31b-it`, so its dollar estimate
           is not a substitute.

WHERE COST COMES FROM. As of langchain-openrouter 0.2.7 `cost` (and
`cost_details`) arrive in `response_metadata` on BOTH paths, streaming included —
the usage-only chunk carries them alongside the token counts. So the ordinary
case needs no second request: `usage_from_message` reads the figure straight off
the response.

`fetch_generation_cost` remains the fallback for when it does not arrive, and
`_finalize_usage` in chat_pipeline.py calls it only when `cost_usd` came back
None. It reads OpenRouter's final accounting from the generation endpoint, keyed
by the id preserved in `response_metadata["id"]`, after the last token has
already been streamed — so it costs latency on the turn's bookkeeping, never on
anything the user is waiting to read.

An earlier version of this module had it the other way round: the package used to
drop cost when streaming, so the generation lookup was the primary path rather
than the fallback. If you are wondering why the fallback looks over-built for
something that rarely fires, that is why.

WHAT LANGSMITH DOES AND DOES NOT READ. Neither number above reaches LangSmith
directly. Its OTel ingestion maps token counts only — `gen_ai.usage.input_tokens`
, `output_tokens`, `total_tokens` and the two `*_token_details` — and there is no
cost attribute in that mapping at all. Dollars are computed server-side by
multiplying those counts against LangSmith's model price map, so a model it has
no price entry for shows tokens and a blank cost no matter what this module
attaches to the response. Making spend appear there is a matter of registering
prices for the `google/gemma-*` slugs in LangSmith, not of sending it more data.
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

    endpoint = settings.chat_endpoint
    api_key = endpoint.api_key
    if not api_key:
        raise OSError("OPENROUTER_API_KEY is not set.")

    if not endpoint.is_openrouter:
        # Any other OpenAI-compatible server (LM Studio locally). ChatOpenAI
        # rather than ChatOpenRouter because the latter's client is built
        # against OpenRouter's own SDK — its base URL is not a parameter, and
        # `session_id` is an OpenRouter dashboard concept with nowhere to land
        # here, so it is dropped rather than forwarded.
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url=endpoint.url,
            temperature=temperature,
            max_tokens=max_tokens,
            streaming=streaming,
        )

    from langchain_openrouter import ChatOpenRouter

    return ChatOpenRouter(
        model=model,
        openrouter_api_key=api_key,
        temperature=temperature,
        max_tokens=max_tokens,
        streaming=streaming,
        session_id=session_id,
        # NO `model_kwargs={"usage": {"include": True}}` HERE, however much the
        # REST API's `usage.include` invites it. `model_kwargs` is splatted
        # straight into `openrouter.chat.Chat.send_async`, whose signature is
        # generated from the API spec and takes no `usage` parameter — so it
        # raises TypeError before a request is ever made, on every call, on both
        # the streaming and non-streaming paths. It does not degrade cost
        # accounting; it removes chat.
        #
        # Cost still arrives: `fetch_generation_cost` reads it from the
        # generation endpoint whenever `usage_from_message` came back without
        # one, which the header describes and which the streaming path has
        # always depended on regardless.
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

    # An LLM_BASE_URL server issues its own completion ids, which OpenRouter's
    # generation endpoint knows nothing about. Looking them up there would spend
    # three requests and a warning per turn to learn what is already known: a
    # local completion has no dollar cost to report.
    if not settings.chat_endpoint.is_openrouter:
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
