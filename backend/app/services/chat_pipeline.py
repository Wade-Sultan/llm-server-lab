from __future__ import annotations

import json
import os
import logging
from typing import AsyncIterator

import openai

from app.data.refbuilds import Build
from app.schemas.chat import BuildProfile, ChatMessage
from app.services.resolver import resolve_build
from app.services.chat_models import ChatModelConfig
from app.core.db import SessionLocal


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

_client: openai.AsyncOpenAI | None = None


def _get_client() -> openai.AsyncOpenAI:
    global _client
    if _client is None:
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise EnvironmentError("OPENROUTER_API_KEY is not set.")
        _client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
        )
    return _client


# ---------------------------------------------------------------------------
# Stage 1 — Extract BuildProfile
# ---------------------------------------------------------------------------

_EXTRACT_SYSTEM = """\
You are the intake analyst for Palladium, a PC-build recommendation service.

Your ONLY job is to read the conversation and extract a structured build profile.
Respond with a JSON object matching this schema EXACTLY (no markdown, no explanation):

{
  "primary_use": "<gaming | video_editing | local_llm | general>",
  "gaming_resolution": "<1080p | 1440p | 4k | null>",
  "budget_tier": "<entry | mid | high | elite>",
  "games": ["<game title>", ...],
  "workloads": ["<workload description>", ...],
  "notes": "<any extra context>"
}

Rules:
 - primary_use must be exactly one of: gaming, video_editing, local_llm, general
 - gaming_resolution is only set when primary_use is "gaming"; otherwise null
 - budget_tier: entry (~$450-700), mid (~$800-1300), high (~$1300-1900), elite (~$2000+)
 - Infer budget_tier from context clues (resolution targets, game types, workload intensity)
   even if the user doesn't state a dollar amount.
 - If the user mentions multiple use cases, pick the most demanding one as primary_use.
 - If you cannot determine a field, use sensible defaults:
   primary_use="general", budget_tier="mid", gaming_resolution=null.
 - Output ONLY the JSON object. No markdown fences. No preamble.
 """


async def extract_profile(messages: list[ChatMessage]) -> BuildProfile:
    """
    Call the model to extract a BuildProfile from the conversation so far.
    Uses a small, fast model since this is a structured extraction task.
    """
    client = _get_client()

    api_messages: list[dict] = [{"role": "system", "content": _EXTRACT_SYSTEM}]
    for msg in messages:
        api_messages.append({
            "role": msg.role if msg.role in ("user", "assistant") else "user",
            "content": msg.content,
        })

    response = await client.chat.completions.create(
        model=ChatModelConfig.get_extract_model(),
        max_tokens=512,
        temperature=0.0,
        messages=api_messages,
    )

    raw = response.choices[0].message.content.strip()

    # Strip markdown fences if the model added them despite instructions
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw[: raw.rfind("```")]
    raw = raw.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("LLM returned invalid JSON for profile extraction: %s", raw)
        data = {
            "primary_use": "general",
            "budget_tier": "mid",
        }

    return BuildProfile(**data)


# ---------------------------------------------------------------------------
# Stage 2 — Stream Recommendation
# ---------------------------------------------------------------------------

_RECOMMEND_SYSTEM = """\
You are Palladium's build advisor — friendly, knowledgeable, and concise.

You have been given a pre-validated PC build that was selected by our \
compatibility engine. Your job is to present it to the user in a clear, \
enthusiastic, and helpful way.

Guidelines:
 - Start with a short sentence acknowledging what the user wants.
 - Present the build name and a one-line summary.
 - Walk through each component with 1-2 sentences explaining why it fits.
 - Mention the approximate total price at the end.
 - Use markdown formatting: **bold** for part names, bullet points for the list.
 - Keep the whole response under 400 words.
 - Do NOT invent specs or prices that aren't in the build data.
 - Do NOT suggest alternative parts — this is the recommended build.
 - Be warm and direct. No filler phrases like "Great question!" or "Absolutely!"
 """


def _format_build_context(
    profile: BuildProfile,
    build_key: str,
    build: Build,
) -> str:
    """Format the resolved build into a context block for the recommendation LLM."""
    parts_text = "\n".join(
        f"  - {p['component']}: {p['brand']} {p['model']} (~${p['approx_price']})"
        for p in build["parts"]
    )
    return f"""\
USER PROFILE:
  Primary use: {profile.primary_use}
  Gaming resolution: {profile.gaming_resolution or "N/A"}
  Budget tier: {profile.budget_tier}
  Games: {", ".join(profile.games) if profile.games else "N/A"}
  Workloads: {", ".join(profile.workloads) if profile.workloads else "N/A"}
  Notes: {profile.notes or "None"}

RESOLVED BUILD: {build_key}
  Label: {build["label"]}
  Description: {build["description"]}
  Approximate Total: ~${build["total_approx"]}

PARTS:
{parts_text}

Present this build to the user now."""


async def stream_recommendation(
    messages: list[ChatMessage],
    profile: BuildProfile,
    build_key: str,
    build: Build,
) -> AsyncIterator[str]:
    """
    Stream the recommendation response token-by-token.
    Yields raw text chunks (not SSE-formatted — the route handles that).
    """
    client = _get_client()

    context = _format_build_context(profile, build_key, build)

    api_messages: list[dict] = [{"role": "system", "content": _RECOMMEND_SYSTEM}]
    for msg in messages:
        api_messages.append({
            "role": msg.role if msg.role in ("user", "assistant") else "user",
            "content": msg.content,
        })
    api_messages.append({"role": "user", "content": context})

    stream = await client.chat.completions.create(
        model=ChatModelConfig.get_recommend_model(),
        max_tokens=1024,
        temperature=0.5,
        messages=api_messages,
        stream=True,
    )
    async for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta


# ---------------------------------------------------------------------------
# Conversational fallback (not enough info yet)
# ---------------------------------------------------------------------------

_ELICIT_SYSTEM = """\
You are Palladium's friendly intake assistant. Your job is to learn enough \
about the user's needs to recommend a PC build.

You need to determine:
1. Primary use case (gaming, video editing, AI/ML, or general productivity)
2. For gaming: target resolution and game types
3. A sense of budget expectations (even vague is fine)

Ask ONE focused follow-up question at a time. Be conversational, not robotic.
Keep responses under 80 words. Use markdown sparingly.

If the user has already provided a configurator payload (JSON with useCases), \
you have enough information — respond with exactly: READY_TO_RECOMMEND
 """


async def stream_elicitation(
    messages: list[ChatMessage],
) -> AsyncIterator[str]:
    """
    Stream a conversational response that gathers more info from the user.
    If the LLM determines there's enough info, it returns "READY_TO_RECOMMEND".
    """
    client = _get_client()

    api_messages: list[dict] = [{"role": "system", "content": _ELICIT_SYSTEM}]
    for msg in messages:
        api_messages.append({
            "role": msg.role if msg.role in ("user", "assistant") else "user",
            "content": msg.content,
        })

    stream = await client.chat.completions.create(
        model=ChatModelConfig.get_elicit_model(),
        max_tokens=256,
        temperature=0.6,
        messages=api_messages,
        stream=True,
    )
    async for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta


# Checks if there's enough info to give a solid recommendation

async def has_enough_info(messages: list[ChatMessage]) -> bool:
    """
    Quick check: does the conversation contain enough signal to extract a
    meaningful BuildProfile? Looks for configurator payloads or sufficient
    natural-language context.
    """
    combined = " ".join(m.content for m in messages if m.role == "user")

    # Fast path: configurator payload is present
    if '"useCases"' in combined or '"use_cases"' in combined:
        return True

    # Fast path: explicit use-case keywords + some detail
    use_keywords = {"gaming", "video editing", "streaming", "ai", "machine learning",
                    "productivity", "nas", "server", "creative", "3d rendering"}
    detail_keywords = {"1080p", "1440p", "4k", "budget", "fps", "vram",
                       "cheap", "expensive", "high-end", "mid-range", "entry"}

    combined_lower = combined.lower()
    has_use = any(kw in combined_lower for kw in use_keywords)
    has_detail = any(kw in combined_lower for kw in detail_keywords)

    return has_use and has_detail


# ---------------------------------------------------------------------------
# Public API — orchestrate the full flow
# ---------------------------------------------------------------------------

async def run_chat_turn(
    messages: list[ChatMessage],
) -> AsyncIterator[dict]:
    """
    Main entry point. Yields SSE-ready dicts:
      {"type": "progress", "step": "...", "message": "..."}
      {"type": "token",    "text": "..."}
      {"type": "build",    "key": "...", "data": {...}}
      {"type": "done"}
    """
    # Check if we have enough info to recommend
    ready = await has_enough_info(messages)

    if not ready:
        # Elicitation mode — gather more info
        buffer = ""
        async for chunk in stream_elicitation(messages):
            # Check if the model signaled readiness mid-stream
            buffer += chunk
            if "READY_TO_RECOMMEND" in buffer:
                ready = True
                break
            yield {"type": "token", "text": chunk}

        if not ready:
            yield {"type": "done"}
            return

    # --- We have enough info: extract → resolve → recommend ---

    yield {"type": "progress", "step": "analyzing", "message": "Analyzing your requirements…"}

    profile = await extract_profile(messages)

    yield {"type": "progress", "step": "resolving", "message": "Selecting your parts…"}
    with SessionLocal() as db:
        build_key, build = resolve_build(profile, db)

    yield {
        "type": "build",
        "key": build_key,
        "data": {
            "label": build["label"],
            "description": build["description"],
            "total_approx": build["total_approx"],
            "parts": build["parts"],
            "profile": profile.model_dump(),
        },
    }

    yield {"type": "progress", "step": "presenting", "message": "Preparing your recommendation…"}

    async for chunk in stream_recommendation(messages, profile, build_key, build):
        yield {"type": "token", "text": chunk}

    yield {"type": "done"}
