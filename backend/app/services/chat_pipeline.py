from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import uuid
from typing import Any, AsyncIterator

import openai

from app.core.config import settings
from app.data.refbuilds import Build
from app.schemas.chat import BuildProfile, BuildRequest, ChatMessage
from app.services.resolver import resolve_build
from app.services.chat_models import ChatModelConfig
from app.core.db import AsyncSessionLocal


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LLM cost/usage capture
# ---------------------------------------------------------------------------
# Every OpenRouter call in a conversation (profile extraction, elicitation,
# recommendation) is billed to the conversation. Streaming calls opt into
# OpenRouter's usage accounting so the final chunk carries tokens + real cost.

_STREAM_USAGE_OPTS: dict[str, Any] = {"stream_options": {"include_usage": True}}


def _extra_body(session_id: str | None) -> dict[str, Any]:
    """
    extra_body kwarg for an OpenRouter chat completion call. Always requests
    usage/cost accounting; adds OpenRouter's `session_id` when one is given so
    every call in a conversation turn groups into one session in OpenRouter's
    dashboard.
    """
    body: dict[str, Any] = {"usage": {"include": True}}
    if session_id:
        body["session_id"] = session_id
    return {"extra_body": body}


def _usage_from_openai(usage_obj: Any) -> dict:
    """Extract {tokens_in, tokens_out, cost_usd} from an OpenAI/OpenRouter usage object."""
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


def _capture_chunk_model(chunk: Any, usage_sink: dict | None) -> None:
    """Record the actual model that served this streaming call (OpenRouter may
    route to a different underlying model than requested)."""
    if usage_sink is None or "model" in usage_sink:
        return
    model = getattr(chunk, "model", None)
    if model:
        usage_sink["model"] = model


def _merge_usage(total: dict, part: dict | None) -> None:
    """Accumulate one call's usage into a running per-turn total (None-safe)."""
    if not part:
        return
    total["tokens_in"] += part.get("tokens_in") or 0
    total["tokens_out"] += part.get("tokens_out") or 0
    total["cost_usd"] += float(part.get("cost_usd") or 0)
    total["llm_call_count"] += 1
    model = part.get("model")
    if model and model not in total["models"]:
        total["models"].append(model)

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
_extract_program_lock = threading.Lock()


def _get_extract_program():
    global _extract_program
    if _extract_program is None:
        # dspy.configure() may only be called by the thread that first calls it.
        # The lifespan warm-up task and an early /chat request both reach here via
        # asyncio.to_thread, i.e. from different worker threads, so the unlocked
        # check-and-set below used to let both call configure_dspy() and the loser
        # would hit "dspy.settings can only be changed by the thread that initially
        # configured it." The lock makes the init happen exactly once.
        with _extract_program_lock:
            if _extract_program is None:
                from app.services.recommender.dspy_pipeline import configure_dspy
                from app.services.recommender.components.extractprofile import load_program

                configure_dspy()
                _extract_program = load_program()
    return _extract_program


def warm_dspy_pipeline() -> None:
    """Force the DSPy/litellm import chain and LM configuration to happen once,
    off the request path (called from main.py's lifespan background task)."""
    _get_extract_program()


def _capture_dspy_usage(prediction: Any, usage_sink: dict) -> None:
    """Pull tokens + cost for the extractprofile DSPy call from the LM history."""
    try:
        import dspy

        from app.services.recommender.recording import extract_usage

        lm = dspy.settings.lm
        history_entry = dict(lm.history[-1]) if getattr(lm, "history", None) else None
        tokens_in, tokens_out, cost, model, _hash = extract_usage(prediction, history_entry)
        usage_sink.update({
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost,
            "model": model,
        })
    except Exception:
        logger.debug("failed to capture dspy usage for extractprofile", exc_info=True)


def _format_conversation(messages: list[ChatMessage]) -> str:
    lines = []
    for msg in messages:
        prefix = "User" if msg.role == "user" else "Assistant"
        lines.append(f"{prefix}: {msg.content}")
    return "\n".join(lines)


async def extract_profile(
    messages: list[ChatMessage],
    usage_sink: dict | None = None,
    session_id: str | None = None,
) -> BuildProfile:
    """Extract a BuildProfile from the conversation using the DSPy module.

    session_id, when given, tags this call with OpenRouter's session_id (via
    dspy.context, which — unlike dspy.configure — is safe to use from any
    thread/task) so it groups with the rest of the turn's calls.
    """
    import dspy

    from app.services.recommender.dspy_pipeline import session_lm

    conversation = _format_conversation(messages)
    program = _get_extract_program()
    with dspy.context(lm=session_lm(session_id)):
        result = await asyncio.to_thread(program, conversation=conversation)

    if usage_sink is not None:
        _capture_dspy_usage(result, usage_sink)

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
    usage_sink: dict | None = None,
    session_id: str | None = None,
) -> AsyncIterator[str]:
    """
    Stream the recommendation response token-by-token.
    Yields raw text chunks (not SSE-formatted — the route handles that).

    If usage_sink is provided, it's filled with this call's tokens + cost from
    OpenRouter's final usage chunk. session_id groups this call with the rest
    of the turn's calls in OpenRouter's dashboard.
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
        **_STREAM_USAGE_OPTS,
        **_extra_body(session_id),
    )
    async for chunk in stream:
        _capture_chunk_model(chunk, usage_sink)
        if getattr(chunk, "usage", None) and usage_sink is not None:
            usage_sink.update(_usage_from_openai(chunk.usage))
        if not chunk.choices:
            continue
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
    usage_sink: dict | None = None,
    session_id: str | None = None,
) -> AsyncIterator[str]:
    """
    Stream a conversational response that gathers more info from the user.
    Readiness to recommend is decided deterministically by `is_profile_complete()`,
    not by the model — this function only ever asks follow-up questions.

    If usage_sink is provided, it's filled with this call's tokens + cost from
    OpenRouter's final usage chunk. session_id groups this call with the rest
    of the turn's calls in OpenRouter's dashboard.
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
        **_STREAM_USAGE_OPTS,
        **_extra_body(session_id),
    )
    async for chunk in stream:
        _capture_chunk_model(chunk, usage_sink)
        if getattr(chunk, "usage", None) and usage_sink is not None:
            usage_sink.update(_usage_from_openai(chunk.usage))
        if not chunk.choices:
            continue
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
# DSPy custom-build path
# ---------------------------------------------------------------------------
# Once the profile is complete, the DSPy pipeline and the reference-build
# resolver are started together. The reference build is the guaranteed
# fallback: if the DSPy run errors out or exceeds the timeout, its result is
# thrown away and the reference build is what gets recommended.

_DSPY_CHAT_TIMEOUT_S = float(os.getenv("DSPY_CHAT_TIMEOUT_S", "180"))

# Sentinel the DSPy runner always enqueues last, so the progress-forwarding
# loop in run_chat_turn knows the pipeline is finished (success or not).
_PIPELINE_DONE = object()

_BUDGET_TIER_USD = {"entry": 1000, "mid": 1500, "high": 2500, "elite": 4000}

# BuildProfile.primary_use → BuildRequest use-case key (must match the keys
# _allocate_budget knows: gaming, aiml, creator, default-anything-else).
_PRIMARY_USE_TO_USE_CASE = {
    "gaming": "gaming",
    "video_editing": "creator",
    "local_llm": "aiml",
    "general": "productivity",
}

# Display order + labels for the assembled BuildCard parts list.
_DSPY_COMPONENT_SLOTS: list[tuple[str, str]] = [
    ("CPU", "cpu_name"),
    ("CPU Cooler", "cooler_name"),
    ("Motherboard", "mobo_name"),
    ("RAM", "ram_name"),
    ("Storage", "storage_name"),
    ("GPU", "gpu_name"),
    ("PSU", "psu_name"),
    ("Case", "case_name"),
    ("Case Fans", "fans_name"),
]


def _profile_to_build_request(profile: BuildProfile) -> BuildRequest:
    """Map the chat-extracted BuildProfile onto the pipeline's BuildRequest."""
    answers: dict[str, str | list[str]] = {}
    if profile.gaming_resolution:
        answers["gaming.resolution"] = profile.gaming_resolution
    if profile.games:
        answers["gaming.games"] = profile.games
    if profile.workloads:
        answers["general.workloads"] = profile.workloads
    if profile.notes:
        answers["general.notes"] = profile.notes
    return BuildRequest(
        use_cases=[_PRIMARY_USE_TO_USE_CASE.get(profile.primary_use, "productivity")],
        budget_usd=_BUDGET_TIER_USD.get(profile.budget_tier, 1500),
        answers=answers,
    )


async def _assemble_dspy_build(state: Any, db) -> dict:
    """Shape a finished DSPyBuildState like a reference Build dict so the
    build SSE event and the recommendation prompt work unchanged.

    Includes part_id + amazon_url per part, same as the reference-build path
    (crud/reference_builds.py._to_build) — BuildCard's Amazon button is gated
    on amazon_url and part_id doubles as its React list key, so both need to
    be resolved here rather than left off like the rest of the payload.
    """
    from app.crud.components import get_part_by_name
    from app.crud.reference_builds import get_amazon_urls_by_part

    resolved: list[tuple[str, str, Any, float | None]] = []
    total = 0.0
    for component, attr in _DSPY_COMPONENT_SLOTS:
        name = getattr(state, attr)
        if not name:
            continue
        part = await get_part_by_name(db, name)
        price = None
        if part is not None and part.street_price_cents is not None:
            price = round(part.street_price_cents / 100, 2)
            total += price
        resolved.append((component, name, part, price))

    amazon_urls = await get_amazon_urls_by_part(
        db, [part.id for _, _, part, _ in resolved if part is not None]
    )

    parts = [
        {
            "component": component,
            "brand": (part.manufacturer if part else None) or "",
            "model": name,
            "approx_price": price,
            "part_id": str(part.id) if part is not None else "",
            "amazon_url": amazon_urls.get(part.id) if part is not None else None,
        }
        for component, name, part, price in resolved
    ]
    return {
        "label": "Custom Build",
        "description": "Assembled component-by-component for your specific needs and budget.",
        "total_approx": round(total),
        "parts": parts,
    }


async def _attach_reference_build(recorder: Any, ref_task: asyncio.Task) -> None:
    """Await the parallel reference-build task and record it on the DSPy session.

    Guarded: a reference-resolution failure must not stop the DSPy run from
    being recorded (it's simply recorded without a reference build).
    """
    try:
        ref_key, ref_build = await ref_task
        recorder.set_reference_build(ref_key, ref_build)
    except Exception:
        logger.warning(
            "reference build resolution failed; DSPy run recorded without it",
            exc_info=True,
        )


async def _run_dspy_build(
    profile: BuildProfile,
    progress_queue: asyncio.Queue,
    ref_task: asyncio.Task,
) -> dict | None:
    """
    Run the full DSPy pipeline for a chat turn on its own DB session.

    Returns a build payload dict shaped like a reference Build, or None on any
    failure — the caller falls back to the reference build. Always enqueues
    _PIPELINE_DONE last. Never raises.

    ref_task is the reference build being resolved in parallel. On success it is
    recorded onto the same build_sessions row as the DSPy run (via the recorder),
    so a completed run carries both the shown DSPy build and the reference build.

    The pipeline's case step returns 3 options meant for a user round-trip;
    the chat flow has no case-picker UI yet, so the best-value option (option 1)
    is auto-selected. All 3 options and their reasons are still recorded.
    """
    from app.models.build_session import BuildSessionStatus
    from app.services.recommender.dspy_pipeline import (
        PIPELINE_VERSION,
        run_pipeline,
        run_pipeline_post_case,
    )
    from app.services.recommender.recording import BuildRecorder

    request = _profile_to_build_request(profile)
    recorder = BuildRecorder(request, PIPELINE_VERSION)

    def _progress(step: str, message: str) -> None:
        progress_queue.put_nowait({"type": "progress", "step": step, "message": message})

    try:
        async with AsyncSessionLocal() as db:
            state = await run_pipeline(
                request, db, progress_callback=_progress, recorder=recorder
            )
            if state.error:
                logger.warning("DSPy pipeline failed; using reference build: %s", state.error)
                return None
            if not state.case_options or not state.case_options[0].get("name"):
                logger.warning("DSPy pipeline produced no case options; using reference build")
                recorder.finish(BuildSessionStatus.ERROR)
                return None

            case_name = state.case_options[0]["name"]
            # Both builds succeeded far enough to record together: attach the
            # reference build before post_case finishes (flushes) the recorder.
            await _attach_reference_build(recorder, ref_task)
            state = await run_pipeline_post_case(state, db, case_name, recorder=recorder)
            if state.error:
                logger.warning("DSPy post-case step failed; using reference build: %s", state.error)
                return None

            return await _assemble_dspy_build(state, db)
    except asyncio.CancelledError:
        # Timed out and cancelled by run_chat_turn — flush telemetry as error.
        recorder.finish(BuildSessionStatus.ERROR)
        raise
    except Exception:
        logger.exception("DSPy pipeline crashed; using reference build")
        return None
    finally:
        progress_queue.put_nowait(_PIPELINE_DONE)


async def _resolve_reference_build(profile: BuildProfile) -> tuple[str, Build]:
    """Resolve the reference build on its own DB session (cached)."""
    async with AsyncSessionLocal() as db:
        return await _resolve_build_cached(profile, db)


# ---------------------------------------------------------------------------
# Public API — orchestrate the full flow
# ---------------------------------------------------------------------------

async def run_chat_turn(
    messages: list[ChatMessage],
    conversation_id: str | None = None,
) -> AsyncIterator[dict]:
    """
    Main entry point. Yields SSE-ready dicts:
      {"type": "progress", "step": "...", "message": "..."}
      {"type": "token",    "text": "..."}
      {"type": "build",    "key": "...", "data": {...}}
      {"type": "usage",    "cost_usd": ..., "tokens_in": ..., "tokens_out": ..., "models": [...]}
      {"type": "done"}

    The "usage" event totals every OpenRouter call made this turn; the route
    consumes it internally (it is not forwarded to the client) to increment the
    conversation's running cost.

    conversation_id (when the caller has one — guests won't) is passed to
    OpenRouter as session_id so every OpenRouter call this turn makes (extract,
    elicit or recommend) groups into one session in OpenRouter's dashboard.
    Falls back to a fresh id scoped to just this turn for guests, so at least
    this turn's calls still group together.
    """
    session_id = conversation_id or str(uuid.uuid4())
    turn_usage = {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "llm_call_count": 0, "models": []}

    # Extract the structured profile up front — the model only ever populates
    # fields here. Whether that's "enough" to recommend is then a hard,
    # code-level decision (is_profile_complete), not something the model says.
    # No progress event yet: the frontend treats any "progress" event as
    # confirmation we're on the recommend path, so it can't fire until we know.
    extract_usage_sink: dict = {}
    profile = await extract_profile(messages, usage_sink=extract_usage_sink, session_id=session_id)
    _merge_usage(turn_usage, extract_usage_sink)

    if not is_profile_complete(profile):
        # Elicitation mode — gather more info, then end the turn.
        elicit_usage_sink: dict = {}
        async for chunk in stream_elicitation(messages, usage_sink=elicit_usage_sink, session_id=session_id):
            yield {"type": "token", "text": chunk}
        _merge_usage(turn_usage, elicit_usage_sink)

        yield {"type": "usage", **turn_usage}
        yield {"type": "done"}
        return

    # --- Profile is complete: run DSPy + reference build in parallel ---
    # Both start as soon as the profile is complete. The DSPy pipeline's
    # progress events stream through while it runs; if it fails or times out,
    # its result is discarded and the reference build is recommended instead.

    yield {"type": "progress", "step": "resolving", "message": "Selecting your parts…"}

    progress_queue: asyncio.Queue = asyncio.Queue()
    ref_task = asyncio.create_task(_resolve_reference_build(profile))
    dspy_task = asyncio.create_task(_run_dspy_build(profile, progress_queue, ref_task))

    deadline = time.monotonic() + _DSPY_CHAT_TIMEOUT_S
    timed_out = False
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            break
        try:
            item = await asyncio.wait_for(progress_queue.get(), timeout=remaining)
        except asyncio.TimeoutError:
            timed_out = True
            break
        if item is _PIPELINE_DONE:
            break
        yield item

    dspy_build: dict | None = None
    if timed_out:
        dspy_task.cancel()
        try:
            await dspy_task
        except asyncio.CancelledError:
            pass
        logger.warning(
            "DSPy pipeline timed out after %.0fs; using reference build",
            _DSPY_CHAT_TIMEOUT_S,
        )
    else:
        dspy_build = await dspy_task  # never raises; None on failure

    if dspy_build is not None:
        # Customer sees the DSPy build. The reference build was already awaited
        # and recorded onto the same session row inside _run_dspy_build; we don't
        # cancel it (it's the recorded comparison), just make sure it's reaped.
        build_key, build = "custom_dspy", dspy_build
        try:
            await ref_task
        except Exception:
            logger.debug("reference build task errored (already recorded/ignored)", exc_info=True)
    else:
        build_key, build = await ref_task

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

    recommend_usage_sink: dict = {}
    async for chunk in stream_recommendation(
        messages, profile, build_key, build, usage_sink=recommend_usage_sink, session_id=session_id
    ):
        yield {"type": "token", "text": chunk}
    _merge_usage(turn_usage, recommend_usage_sink)

    yield {"type": "usage", **turn_usage}
    yield {"type": "done"}
