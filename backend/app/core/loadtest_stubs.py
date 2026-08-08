"""Stub LMs used when a request opts into load-test mode.

Both stubs are local and make no network calls of any kind — that is the whole
point. See app/core/loadtest.py for how a request is routed here.

They deliberately imitate the *shape* of the real thing rather than returning
instantly: an LM that answers in 0ms would make a load test measure a service
that does not exist. The delays below are crude but keep concurrency, SSE
framing and connection-holding behaviour in the right ballpark. Tune with
LOAD_TEST_STUB_TTFT_MS / LOAD_TEST_STUB_TOKEN_MS; set both to 0 to measure pure
service overhead with the LM removed entirely.
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

# Time-to-first-byte, mimicking an LLM thinking before it streams.
_TTFT_S = int(os.environ.get("LOAD_TEST_STUB_TTFT_MS", "400")) / 1000
# Inter-token delay while streaming.
_TOKEN_S = int(os.environ.get("LOAD_TEST_STUB_TOKEN_MS", "10")) / 1000

_STUB_TEXT = (
    "This is a stubbed response served in load-test mode. No language model "
    "was called and no tokens were spent. "
)


# ---------------------------------------------------------------------------
# Raw OpenAI/OpenRouter client, for the discovery pipeline
# ---------------------------------------------------------------------------
# Two client stubs, because two real clients are in use: chat goes through
# LangChain (StubChatModel below) and discovery stays on the openai SDK for its
# response_format and multimodal parts. See
# app/services/discovery/openrouter_client.py for why they diverged.


class _StubCompletions:
    async def create(self, *, model: str = "stub", **kwargs: Any) -> Any:
        await asyncio.sleep(_TTFT_S)
        return SimpleNamespace(
            model=model,
            usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0, cost=0.0),
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=_STUB_TEXT),
                    finish_reason="stop",
                )
            ],
        )


class StubOpenAIClient:
    """Drop-in for openai.AsyncOpenAI covering the surface discovery uses."""

    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=_StubCompletions())


# ---------------------------------------------------------------------------
# Chat model (LangChain), standing in for ChatOpenRouter
# ---------------------------------------------------------------------------


def _stub_usage() -> dict[str, Any]:
    """Token counts for a stubbed call.

    Zeros rather than invented figures: the point of load-test mode is that no
    tokens were spent, and a fabricated count would land in
    conversations.total_tokens_* and misreport real usage.
    """
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "input_token_details": {},
        "output_token_details": {},
    }


def _stub_response_metadata(model: str) -> dict[str, Any]:
    # cost is explicitly 0.0 rather than absent: the per-conversation cost
    # column should record that this turn was free, not that its cost is
    # unknown. No generation id, so nothing tries to look the cost up over the
    # network — see app/services/llm/openrouter.py.
    return {"model_name": model, "cost": 0.0, "model_provider": "openrouter"}


class StubChatModel(BaseChatModel):
    """Drop-in for ChatOpenRouter that never leaves the process.

    Implements the BaseChatModel hooks app/services/llm/ actually calls —
    `ainvoke` (via `_agenerate`) and `astream` (via `_astream`). The synchronous
    halves raise, because reaching them would mean a call site changed and a
    load test had quietly started billing OpenRouter.
    """

    model_name: str = "stub"

    @property
    def _llm_type(self) -> str:
        return "stub-chat-model"

    def _generate(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("load-test stub is async-only")

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        await asyncio.sleep(_TTFT_S)
        message = AIMessage(
            content=_STUB_TEXT,
            usage_metadata=_stub_usage(),
            response_metadata=_stub_response_metadata(self.model_name),
        )
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        """Stream word by word, then a usage-only chunk.

        The trailing empty-content chunk carrying usage is what ChatOpenRouter
        really emits when stream_options.include_usage is set, and
        chat_pipeline._stream_text sums across every chunk to find it — so the
        shape matters as much as the timing.
        """
        await asyncio.sleep(_TTFT_S)

        for word in (_STUB_TEXT * 2).split(" "):
            if _TOKEN_S:
                await asyncio.sleep(_TOKEN_S)
            yield ChatGenerationChunk(message=AIMessageChunk(content=word + " "))

        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content="",
                usage_metadata=_stub_usage(),
                response_metadata=_stub_response_metadata(self.model_name),
            )
        )


# ---------------------------------------------------------------------------
# DSPy
# ---------------------------------------------------------------------------

# The ChatAdapter system prompt declares output fields in a stable, parseable
# block:
#
#     Your output fields are:
#     1. `choice` (str): the chosen part
#     2. `score` (int):
#
# Parsing that is what lets one stub serve all eleven Decide* modules without
# hardcoding a canned answer per signature. dspy.utils.DummyLM was the obvious
# alternative and does not work here: it replays fixed dicts, so every module
# would need its own answer set kept in sync with its signature by hand.
_OUTPUT_BLOCK = re.compile(
    r"Your output fields are:\s*\n(.*?)(?:\n\s*\n|All interactions)", re.DOTALL
)
_FIELD = re.compile(r"^\s*\d+\.\s*`([^`]+)`\s*\(([^)]*)\)", re.MULTILINE)


# Fields whose *value* decides which branch the request takes, overridden so a
# load test drives the full build pipeline rather than parking in elicitation.
#
# These are declared `str` in the signatures rather than Literal, so the generic
# placeholder below returns "stub" — and is_profile_complete() reads "stub" as
# an unrecognised primary_use, judges the profile incomplete, and every virtual
# user loops forever on the elicitation path. The expensive path (eleven Decide*
# modules, one LM call each) would then never be exercised at all, which is the
# path most worth load testing.
#
# Values must stay valid against app/schemas/chat.py. If a load test suddenly
# spends all its time in elicitation, check here first.
_BRANCH_FIELDS = {
    "primary_use": "gaming",
    "budget_tier": "mid",
    "gaming_resolution": "1440p",
    "gaming_fps": "144",
}


def _placeholder(type_str: str, name: str = "") -> str:
    """A value the ChatAdapter can parse back into the declared type."""
    if name in _BRANCH_FIELDS:
        return _BRANCH_FIELDS[name]

    t = type_str.strip()

    # Literal['a', 'b'] -> 'a'. Enum-typed fields reject anything else, so
    # guessing a generic string here would fail validation.
    literals = re.findall(r"['\"]([^'\"]+)['\"]", t)
    if t.startswith("Literal") and literals:
        return str(literals[0])

    if t.startswith(("list", "List")):
        return "[]"
    if t.startswith(("dict", "Dict")):
        return "{}"
    if "bool" in t:
        return "false"
    if "float" in t:
        return "0.0"
    if "int" in t:
        return "0"
    return "stub"


def _render_response(messages: list[dict[str, Any]]) -> str:
    system = next(
        (m.get("content", "") for m in messages if m.get("role") == "system"), ""
    )
    block = _OUTPUT_BLOCK.search(system or "")
    fields = _FIELD.findall(block.group(1)) if block else []

    parts = [
        f"[[ ## {name} ## ]]\n{_placeholder(type_str, name)}"
        for name, type_str in fields
    ]
    parts.append("[[ ## completed ## ]]")
    return "\n\n".join(parts)


def make_stub_lm() -> Any:
    """A dspy.LM that answers from the prompt's own field declarations."""
    import dspy

    class StubLM(dspy.LM):
        def __init__(self) -> None:
            # model_type="chat" so DSPy hands us `messages` rather than a
            # flat prompt string, which is what _render_response reads.
            super().__init__(model="stub/load-test", model_type="chat", cache=False)

        def forward(
            self,
            prompt: str | None = None,
            messages: list[dict[str, Any]] | None = None,
            **kwargs: Any,
        ) -> Any:
            text = _render_response(messages or [])
            # Shaped like a litellm ModelResponse: DSPy reads .choices[].message
            # and, when track_usage is on, .usage.
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=text, tool_calls=None)
                    )
                ],
                # A plain dict, not a namespace: DSPy does dict(response.usage)
                # when recording history, which needs a mapping.
                usage={
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "cost": 0.0,
                },
                model="stub/load-test",
            )

        async def aforward(
            self,
            prompt: str | None = None,
            messages: list[dict[str, Any]] | None = None,
            **kwargs: Any,
        ) -> Any:
            return self.forward(prompt=prompt, messages=messages, **kwargs)

    return StubLM()
