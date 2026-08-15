"""Raw OpenRouter client for the discovery pipeline.

WHY THIS IS NOT app/services/llm/. The chat pipeline moved to LangChain's
ChatOpenRouter so LangSmith can tally its spend as real LLM runs. Discovery did
not, for two reasons that are about this pipeline rather than about preference:

  * It leans on `response_format` with a per-category JSON schema, and on
    multimodal content parts (rasterized PDF spec sheets). Both are expressed
    natively here and would need re-expressing against LangChain's abstractions
    for no gain.
  * It is a CronJob, not a conversation. Its spend belongs to a catalog refresh,
    not to a `conversations` row, and nothing asks LangSmith to attribute it.

These three helpers were previously imported from `chat_pipeline`, which is why
they look like chat code. They moved here when chat stopped using them, so that
their only caller owns them.
"""

from __future__ import annotations

from typing import Any

import openai

from app.core.config import settings

_client: openai.AsyncOpenAI | None = None


def get_client() -> openai.AsyncOpenAI:
    """The shared OpenRouter client.

    The load-test check comes before the cached client so a stubbed request
    cannot fall through to the real one, and is deliberately not itself cached —
    the decision is per-request, not per-process.
    """
    global _client
    from app.core.loadtest import is_load_test

    if is_load_test():
        from app.core.loadtest_stubs import StubOpenAIClient

        return StubOpenAIClient()  # type: ignore[return-value]

    if _client is None:
        # discovery_endpoint, not chat_endpoint: this pipeline needs a
        # multimodal model that honours a JSON schema, so an environment that
        # moves chat to a local server can leave discovery on OpenRouter via
        # DISCOVERY_LLM_BASE_URL. See app/core/config.py.
        endpoint = settings.discovery_endpoint
        api_key = endpoint.api_key
        if not api_key:
            raise OSError("OPENROUTER_API_KEY is not set.")
        _client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=endpoint.url,
        )
    return _client


def extra_body(session_id: str | None) -> dict[str, Any]:
    """extra_body kwarg for an OpenRouter completion.

    Always requests usage/cost accounting; adds OpenRouter's `session_id` when
    one is given so every call in a discovery run groups into one session in
    OpenRouter's dashboard.

    Both fields are OpenRouter extensions, so they are omitted entirely when
    LLM_BASE_URL points somewhere else — extra_body is sent verbatim in the
    request, and a stricter OpenAI-compatible server rejects the whole call over
    a field it does not recognise.
    """
    if not settings.discovery_endpoint.is_openrouter:
        return {}

    body: dict[str, Any] = {"usage": {"include": True}}
    if session_id:
        body["session_id"] = session_id
    return {"extra_body": body}


def usage_from_openai(usage_obj: Any) -> dict:
    """Extract {tokens_in, tokens_out, cost_usd} from an OpenRouter usage object."""
    if usage_obj is None:
        return {}
    tokens_in = getattr(usage_obj, "prompt_tokens", None)
    tokens_out = getattr(usage_obj, "completion_tokens", None)
    # OpenRouter returns real dollar cost as an extra `cost` field on usage.
    cost = getattr(usage_obj, "cost", None)
    if cost is None:
        extra = getattr(usage_obj, "model_extra", None) or {}
        cost = extra.get("cost")
    return {"tokens_in": tokens_in, "tokens_out": tokens_out, "cost_usd": cost}
