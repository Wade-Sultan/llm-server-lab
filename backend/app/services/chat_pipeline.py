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
    """Force the DSPy/litellm import chain, LM configuration, and Decide*
    module construction to happen once, off the request path (called from
    main.py's lifespan background task)."""
    _get_extract_program()

    # Distinct from the extract program above: these are the ten build-step
    # modules, each of which reads its GEPA weights file when first built.
    from app.services.recommender.dspy_pipeline import load_all_programs

    load_all_programs()


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

    def _opt(value: str) -> str | None:
        """Map the extraction model's 'none'/empty sentinel to a real None."""
        v = (value or "").strip()
        return None if not v or v.lower() == "none" else v

    return BuildProfile(
        primary_use=result.primary_use,
        gaming_resolution=_opt(result.gaming_resolution),
        gaming_fps=_opt(result.gaming_fps),
        streaming_style=_opt(result.streaming_style),
        ai_workload=_opt(result.ai_workload),
        ai_model_scale=_opt(result.ai_model_scale),
        editing_resolution=_opt(result.editing_resolution),
        rendering_software=_opt(result.rendering_software),
        workload_intensity=_opt(result.workload_intensity),
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


def _profile_details(profile: BuildProfile) -> str:
    """Render the use-case-specific profile fields that were actually inferred."""
    bits = []
    if profile.gaming_resolution:
        bits.append(f"resolution: {profile.gaming_resolution}")
    if profile.gaming_fps:
        bits.append(f"target fps: {profile.gaming_fps}")
    if profile.streaming_style:
        bits.append(f"streaming style: {profile.streaming_style}")
    if profile.ai_workload:
        bits.append(f"AI workload: {profile.ai_workload}")
    if profile.ai_model_scale:
        bits.append(f"model scale: {profile.ai_model_scale}")
    if profile.editing_resolution:
        bits.append(f"footage resolution: {profile.editing_resolution}")
    if profile.rendering_software:
        bits.append(f"rendering software: {profile.rendering_software}")
    if profile.workload_intensity:
        bits.append(f"workload intensity: {profile.workload_intensity}")
    return "; ".join(bits) if bits else "N/A"


def _format_build_context(
    profile: BuildProfile,
    build_key: str,
    build: Build,
) -> str:
    """Format the resolved build into a context block for the recommendation LLM.

    approx_price/total_approx are in cents (see _assemble_dspy_build's docstring
    for the convention); divide by 100 to show the LLM real dollar figures.
    """
    parts_text = "\n".join(
        f"  - {p['component']}: {p['brand']} {p['model']} "
        f"(~${p['approx_price'] / 100:.0f})" if p.get("approx_price") is not None else
        f"  - {p['component']}: {p['brand']} {p['model']}"
        for p in build["parts"]
    )
    return f"""\
USER PROFILE:
  Primary use: {profile.primary_use}
  Use-case details: {_profile_details(profile)}
  Budget tier: {profile.budget_tier}
  Games: {", ".join(profile.games) if profile.games else "N/A"}
  Workloads: {", ".join(profile.workloads) if profile.workloads else "N/A"}
  Notes: {profile.notes or "None"}

RESOLVED BUILD: {build_key}
  Label: {build["label"]}
  Description: {build["description"]}
  Approximate Total: ~${build["total_approx"] / 100:.0f}

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
1. Primary use case (gaming, streaming, video editing, 3D rendering, AI/ML,
   software development, music production, or general productivity)
2. For gaming (or streaming gameplay): target resolution AND target frame rate, plus game types
3. For streaming: whether they stream while gaming or camera/IRL content only
4. For AI/ML: the workload (running models, training/fine-tuning, image generation)
   and roughly how large the models are
5. For video editing: the resolution of the footage they edit
6. For 3D rendering: which software/renderer they work in
7. For software development or music production: how heavy the workload is
   (codebase size, VMs/containers; track and plugin counts)
8. Budget expectations (even vague is fine)

Ask ONE focused follow-up question at a time. Be conversational. Keep responses under 80 words.

Do not describe or recommend a build yourself. Do not say the build is ready, that you have
enough information, or that a recommendation is coming — the handoff to the build recommender
happens automatically and silently once every required item is filled in; you will be told
exactly what's still missing below, so just ask about that.
"""


# Mirrors is_profile_complete()'s branches so the elicitation model is told
# exactly which field it's blocked on, instead of judging "enough info" for
# itself from the raw conversation (which is what let it drift into saying
# things like "the build recommender will be back with your build").
def _missing_fields(profile: BuildProfile) -> list[str]:
    if profile.primary_use == "unknown":
        return ["their primary use case (gaming, streaming, video editing, "
                "3D rendering, AI/ML, software development, music production, "
                "or general productivity)"]

    missing: list[str] = []
    use = profile.primary_use
    if use == "gaming":
        if profile.gaming_resolution is None:
            missing.append("target gaming resolution")
        if profile.gaming_fps is None:
            missing.append("target frame rate")
    elif use == "streaming":
        if profile.streaming_style is None:
            missing.append("whether they stream while gaming or camera/IRL content only")
        elif profile.streaming_style == "while_gaming":
            if profile.gaming_resolution is None:
                missing.append("target gaming resolution")
            if profile.gaming_fps is None:
                missing.append("target frame rate")
    elif use == "ai":
        if profile.ai_workload is None:
            missing.append("the AI workload (running models, training/fine-tuning, or image generation)")
        elif profile.ai_workload in ("inference", "training") and profile.ai_model_scale is None:
            missing.append("roughly how large the models are")
    elif use == "video_editing":
        if profile.editing_resolution is None:
            missing.append("the resolution of the footage they edit")
    elif use == "3d_rendering":
        if profile.rendering_software is None:
            missing.append("which 3D software/renderer they work in")
    elif use in ("software_dev", "music_production"):
        if profile.workload_intensity is None:
            missing.append("how heavy the workload is (codebase size/VMs, or track/plugin counts)")

    if profile.budget_tier == "unknown":
        missing.append("budget expectations")

    return missing


async def stream_elicitation(
    messages: list[ChatMessage],
    missing_fields: list[str] | None = None,
    price_estimate: int | None = None,
    usage_sink: dict | None = None,
    session_id: str | None = None,
) -> AsyncIterator[str]:
    """
    Stream a conversational response that gathers more info from the user.
    Readiness to recommend is decided deterministically by `is_profile_complete()`,
    not by the model — this function only ever asks follow-up questions.

    missing_fields (from _missing_fields(), computed off the same profile
    is_profile_complete() just checked) tells the model exactly what's still
    blocking, so it doesn't have to re-judge "enough info" from raw text.

    price_estimate, when given (budget is the only missing field), is a
    reference build's total already rounded to the nearest $100 — the model
    is told to mention this figure, not invent its own.

    If usage_sink is provided, it's filled with this call's tokens + cost from
    OpenRouter's final usage chunk. session_id groups this call with the rest
    of the turn's calls in OpenRouter's dashboard.
    """
    client = _get_client()

    system_content = _ELICIT_SYSTEM
    if missing_fields:
        system_content += (
            "\n\nStill missing, in priority order: "
            + "; ".join(missing_fields)
            + ".\nAsk about the FIRST missing item only."
        )
    if price_estimate is not None:
        system_content += (
            f"\n\nBased on everything they've described so far, a build like this would run "
            f"about ${price_estimate:,}. Mention that figure naturally while asking about "
            f"their budget — don't invent a different number."
        )

    api_messages: list[dict] = [{"role": "system", "content": system_content}]
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
    inferred, plus the use-case-specific fields that meaningfully fork the
    build: gaming needs resolution AND target frame rate; streaming needs a
    style (and resolution + frame rate when streaming while gaming); AI needs
    a workload (and a model scale for LLM workloads); video editing needs
    footage resolution; 3D rendering needs the software used; software dev and
    music production need a workload intensity.
    """
    if profile.primary_use == "unknown":
        return False
    if profile.budget_tier == "unknown":
        return False

    use = profile.primary_use
    if use == "gaming":
        return profile.gaming_resolution is not None and profile.gaming_fps is not None
    if use == "streaming":
        if profile.streaming_style is None:
            return False
        if profile.streaming_style == "while_gaming":
            return profile.gaming_resolution is not None and profile.gaming_fps is not None
        return True
    if use == "ai":
        if profile.ai_workload is None:
            return False
        if profile.ai_workload in ("inference", "training") and profile.ai_model_scale is None:
            return False
        return True
    if use == "video_editing":
        return profile.editing_resolution is not None
    if use == "3d_rendering":
        return profile.rendering_software is not None
    if use in ("software_dev", "music_production"):
        return profile.workload_intensity is not None
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
# _allocate_budget knows: gaming, streaming, creator, rendering, aiml, dev,
# audio, default-anything-else).
_PRIMARY_USE_TO_USE_CASE = {
    "gaming": "gaming",
    "streaming": "streaming",
    "video_editing": "creator",
    "3d_rendering": "rendering",
    "ai": "aiml",
    "software_dev": "dev",
    "music_production": "audio",
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
    if profile.gaming_fps:
        answers["gaming.target_fps"] = profile.gaming_fps
    if profile.streaming_style:
        answers["streaming.style"] = profile.streaming_style
    if profile.ai_workload:
        answers["ai.workload"] = profile.ai_workload
    if profile.ai_model_scale:
        answers["ai.model_scale"] = profile.ai_model_scale
    if profile.editing_resolution:
        answers["editing.footage_resolution"] = profile.editing_resolution
    if profile.rendering_software:
        answers["rendering.software"] = profile.rendering_software
    if profile.workload_intensity:
        answers["general.workload_intensity"] = profile.workload_intensity
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

    total_approx (and approx_price) are in cents, not dollars — BuildCard
    renders `data.total_approx / 100` directly, same convention as every other
    *_cents column in this codebase (street_price_cents, etc).
    """
    from app.crud.components import get_part_by_name, resolve_part_price_cents
    from app.crud.reference_builds import get_amazon_urls_by_part

    resolved: list[tuple[str, str, Any, int | None]] = []
    total_cents = 0
    for component, attr in _DSPY_COMPONENT_SLOTS:
        name = getattr(state, attr)
        if not name:
            continue
        part = await get_part_by_name(db, name)
        # Grouped parts (GPU/PSU/RAM/Storage) carry price on their group, not the
        # exact pc_parts row — resolve_part_price_cents handles both.
        price_cents = await resolve_part_price_cents(db, part) if part is not None else None
        if price_cents is not None:
            total_cents += price_cents
        resolved.append((component, name, part, price_cents))

    amazon_urls = await get_amazon_urls_by_part(
        db, [part.id for _, _, part, _ in resolved if part is not None]
    )

    parts = [
        {
            "component": component,
            "brand": (part.manufacturer if part else None) or "",
            "model": name,
            "approx_price": price_cents,
            "part_id": str(part.id) if part is not None else "",
            "amazon_url": amazon_urls.get(part.id) if part is not None else None,
        }
        for component, name, part, price_cents in resolved
    ]
    return {
        "label": "Custom Build",
        "description": "Assembled component-by-component for your specific needs and budget.",
        "total_approx": total_cents,
        "parts": parts,
    }


async def _attach_reference_build(recorder: Any, ref_task: asyncio.Task) -> None:
    """Await the parallel reference-build task and record it on the DSPy session.

    Guarded: a reference-resolution failure must not stop the DSPy run from
    being recorded (it's simply recorded without a reference build).
    """
    try:
        ref_key, ref_build, _ = await ref_task
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


async def _load_cached_reference_build(conversation_id: str | None) -> tuple[str, Build] | None:
    """Load the reference build already cached on this conversation, if any.

    Returns None when there's no conversation_id, it's not a real UUID (guest
    turns pass a fresh scratch id — see run_chat_turn), no Conversation row
    exists yet (first turn, before _save_turn has created it), or nothing has
    been cached onto it yet.
    """
    if not conversation_id:
        return None
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        return None

    from app.models.conversation import Conversation

    async with AsyncSessionLocal() as db:
        conversation = await db.get(Conversation, conv_uuid)
    if conversation and conversation.reference_build_key and conversation.reference_build:
        return conversation.reference_build_key, conversation.reference_build
    return None


async def _get_reference_build(
    profile: BuildProfile,
    conversation_id: str | None,
    assumed_budget_tier: str | None = None,
) -> tuple[str, Build, bool]:
    """
    Resolve the reference build for this conversation, reusing whatever was
    already cached on it from an earlier turn instead of re-resolving.

    Once a conversation has a cached reference build it's frozen for the rest
    of the conversation — including if it was first resolved as a rough
    estimate under assumed_budget_tier before the user's real budget was
    known — so it stays the exact build the user was already told about.

    Returns (build_key, build, was_cached).
    """
    cached = await _load_cached_reference_build(conversation_id)
    if cached is not None:
        return cached[0], cached[1], True

    resolve_profile = profile
    if assumed_budget_tier is not None:
        resolve_profile = profile.model_copy(update={"budget_tier": assumed_budget_tier})

    async with AsyncSessionLocal() as db:
        build_key, build = await _resolve_build_cached(resolve_profile, db)
    return build_key, build, False


def _round_to_nearest_hundred_usd(total_approx_cents: int) -> int:
    dollars = total_approx_cents / 100
    return int(round(dollars / 100) * 100)


def _build_payload(build: Build, profile: BuildProfile) -> dict:
    return {
        "label": build["label"],
        "description": build["description"],
        "total_approx": build["total_approx"],
        "parts": build["parts"],
        "profile": profile.model_dump(),
    }


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
      {"type": "reference_estimate", "key": "...", "data": {...}}
      {"type": "build",    "key": "...", "data": {...}}
      {"type": "usage",    "cost_usd": ..., "tokens_in": ..., "tokens_out": ..., "models": [...]}
      {"type": "done"}

    "reference_estimate" fires at most once per conversation, the first time
    a reference build is resolved for it (whether that's the budget-still-
    unknown estimate or the one resolved alongside a completed turn) — the
    route consumes it internally to cache the build onto the conversation
    row; it is not meant to replace the "build" event on the client.

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
        missing_fields = _missing_fields(profile)

        # Budget is the only thing left: resolve a reference build now (a
        # "mid" tier guess, since the real budget isn't known yet) so this
        # turn can tell the user what to expect to pay while it asks. Once
        # resolved, it's cached on the conversation and never re-resolved.
        price_estimate: int | None = None
        if missing_fields == ["budget expectations"]:
            est_key, est_build, est_cached = await _get_reference_build(
                profile, conversation_id, assumed_budget_tier="mid"
            )
            price_estimate = _round_to_nearest_hundred_usd(est_build["total_approx"])
            if not est_cached:
                yield {
                    "type": "reference_estimate",
                    "key": est_key,
                    "data": _build_payload(est_build, profile),
                }

        elicit_usage_sink: dict = {}
        async for chunk in stream_elicitation(
            messages,
            missing_fields=missing_fields,
            price_estimate=price_estimate,
            usage_sink=elicit_usage_sink,
            session_id=session_id,
        ):
            yield {"type": "token", "text": chunk}
        _merge_usage(turn_usage, elicit_usage_sink)

        yield {"type": "usage", **turn_usage}
        yield {"type": "done"}
        return

    # --- Profile is complete: run DSPy + reference build in parallel ---
    # Both start as soon as the profile is complete. The DSPy pipeline's
    # progress events stream through while it runs; if it fails or times out,
    # its result is discarded and the reference build is recommended instead.

    yield {"type": "progress", "step": "resolving", "message": "Building your PC…"}

    progress_queue: asyncio.Queue = asyncio.Queue()
    ref_task = asyncio.create_task(_get_reference_build(profile, conversation_id))
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
            ref_key, ref_build, ref_cached = await ref_task
            if not ref_cached:
                yield {
                    "type": "reference_estimate",
                    "key": ref_key,
                    "data": _build_payload(ref_build, profile),
                }
        except Exception:
            logger.debug("reference build task errored (already recorded/ignored)", exc_info=True)
    else:
        build_key, build, ref_cached = await ref_task
        if not ref_cached:
            yield {
                "type": "reference_estimate",
                "key": build_key,
                "data": _build_payload(build, profile),
            }

    yield {
        "type": "build",
        "key": build_key,
        "data": _build_payload(build, profile),
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
