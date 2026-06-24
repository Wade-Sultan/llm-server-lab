from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

import openai

from app.core.config import settings
from app.data.refbuilds import Build
from app.schemas.chat import BuildProfile, ChatMessage
from app.services.resolver import resolve_build
from app.services.chat_models import ChatModelConfig
from app.services.recommender.components.extractprofile import load_program as _load_extract_program
from app.core.db import AsyncSessionLocal


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

_client: openai.AsyncOpenAI | None = None


def _get_client() -> openai.AsyncOpenAI:
    global _client
    if _client is None:
        api_key = settings.OPENROUTER_API_KEY
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

_extract_program = None


def _get_extract_program():
    global _extract_program
    if _extract_program is None:
        _extract_program = _load_extract_program()
    return _extract_program


def _format_conversation(messages: list[ChatMessage]) -> str:
    lines = []
    for msg in messages:
        prefix = "User" if msg.role == "user" else "Assistant"
        lines.append(f"{prefix}: {msg.content}")
    return "\n".join(lines)


async def extract_profile(messages: list[ChatMessage]) -> BuildProfile:
    """Extract a BuildProfile from the conversation using the DSPy module."""
    conversation = _format_conversation(messages)
    program = _get_extract_program()
    result = await asyncio.to_thread(program, conversation=conversation)

    games = [g.strip() for g in result.games.split(",") if g.strip()]
    workloads = [w.strip() for w in result.workloads.split(",") if w.strip()]
    gaming_resolution = None if result.gaming_resolution == "none" else result.gaming_resolution

    return BuildProfile(
        primary_use=result.primary_use,
        gaming_resolution=gaming_resolution,
        budget_tier=result.budget_tier,
        games=games,
        workloads=workloads,
        notes=result.notes,
    )


# ---------------------------------------------------------------------------
# Stage 2 — Stream Recommendation
# ---------------------------------------------------------------------------

_RECOMMEND_SYSTEM = """\
You are Palladium's build advisor — friendly, knowledgeable, concise.

The user is about to see a BuildCard with the full parts list, pricing, and
description, so you do NOT need to repeat any of that. Your job is just a
short, warm lead-in message:
 - Briefly acknowledge what the user is looking for.
 - In one or two sentences, say you've picked out a build for them and why
   it fits at a high level (no need to name individual parts).
 - Do NOT list components, specs, or prices — that's all in the BuildCard.
 - Do NOT suggest alternatives — this is the recommended build.
 - Keep the response under 50 words. No filler phrases.
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

Write the short lead-in message now. The BuildCard above is already shown to the user."""


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
        max_tokens=128,
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
You are Palladium's friendly intake assistant. Learn the user's needs to recommend a PC build.

Determine:
1. Primary use case (gaming, video editing, AI/ML, or general productivity)
2. For gaming: target resolution and game types
3. Budget expectations (even vague is fine)

Ask ONE focused follow-up question at a time. Be conversational. Keep responses under 80 words.

Do not describe or recommend a build yourself; a separate step handles that once enough
information has been gathered.
"""


async def stream_elicitation(
    messages: list[ChatMessage],
) -> AsyncIterator[str]:
    """
    Stream a conversational response that gathers more info from the user.
    Readiness to recommend is decided deterministically by `is_profile_complete()`,
    not by the model — this function only ever asks follow-up questions.
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


# Checks if the extracted profile actually carries enough signal to recommend.
# This is a hard, code-level decision over structured fields the model
# populates — the model never decides readiness itself.

def is_profile_complete(profile: BuildProfile) -> bool:
    """
    A profile is complete once primary_use and budget_tier have both been
    inferred, and — for gaming specifically — a resolution has been inferred too.
    """
    if profile.primary_use == "unknown":
        return False
    if profile.budget_tier == "unknown":
        return False
    if profile.primary_use == "gaming" and profile.gaming_resolution is None:
        return False
    return True


# ---------------------------------------------------------------------------
# Build resolution cache — profile → build mapping is deterministic
# ---------------------------------------------------------------------------

_resolve_cache: dict[str, tuple[str, Build]] = {}


async def _resolve_build_cached(
    profile: BuildProfile,
    db,
) -> tuple[str, Build]:
    """Resolve a build with in-memory caching to avoid repeated DB queries."""
    cache_key = f"{profile.primary_use}:{profile.gaming_resolution}:{profile.budget_tier}"
    if cache_key in _resolve_cache:
        return _resolve_cache[cache_key]

    build_key, build = await resolve_build(profile, db)
    _resolve_cache[cache_key] = (build_key, build)
    return build_key, build


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
    # Extract the structured profile up front — the model only ever populates
    # fields here. Whether that's "enough" to recommend is then a hard,
    # code-level decision (is_profile_complete), not something the model says.
    # No progress event yet: the frontend treats any "progress" event as
    # confirmation we're on the recommend path, so it can't fire until we know.
    profile = await extract_profile(messages)

    if not is_profile_complete(profile):
        # Elicitation mode — gather more info, then end the turn.
        async for chunk in stream_elicitation(messages):
            yield {"type": "token", "text": chunk}

        yield {"type": "done"}
        return

    # --- Profile is complete: resolve → recommend ---

    yield {"type": "progress", "step": "resolving", "message": "Selecting your parts…"}
    async with AsyncSessionLocal() as db:
        build_key, build = await _resolve_build_cached(profile, db)

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
