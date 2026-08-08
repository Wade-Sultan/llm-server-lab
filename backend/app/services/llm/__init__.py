"""OpenRouter access for the chat pipeline, through LangChain.

Everything here exists so that LangSmith sees real LLM runs — with token counts
attached — rather than the opaque HTTP spans a raw `openai.AsyncOpenAI` client
produces. `ChatOpenRouter` populates `usage_metadata` automatically, which is
what makes the per-project spend view in LangSmith work at all.

The DSPy build steps deliberately do NOT come through here. They reach
OpenRouter via litellm, and `app/services/recommender/recording.py` reads
`dspy.settings.lm.history` for their cost — swapping their LM would break that
and the GEPA tooling with it. They reach LangSmith through litellm's own
callback instead; see app/core/tracing.py.
"""

from app.services.llm.openrouter import (
    fetch_generation_cost,
    get_chat_model,
    usage_from_message,
)

__all__ = ["fetch_generation_cost", "get_chat_model", "usage_from_message"]
