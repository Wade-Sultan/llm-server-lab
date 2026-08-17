from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any, NamedTuple

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from app.core.db import AsyncSessionLocal
from app.core.tracing import attach_run_metadata, attach_thread
from app.data.refbuilds import Build
from app.schemas.chat import (
    NO_BUDGET_CEILING,
    BuildProfile,
    BuildRequest,
    ChatMessage,
    UserPreferences,
)
from app.services.chat_models import ChatModelConfig
from app.services.llm import fetch_generation_cost, get_chat_model, usage_from_message
from app.services.resolver import resolve_build

logger = logging.getLogger(__name__)


# --- LLM cost/usage capture ---------------------------------------------------
# Every OpenRouter call in a conversation (profile extraction, routing,
# elicitation, recommendation) is billed to the conversation.
#
# The chat calls go through LangChain's ChatOpenRouter (app/services/llm/), so
# LangSmith sees real LLM runs with token counts rather than opaque HTTP spans.
# Dollar cost is a separate lookup on the streaming paths — see that module's
# header for why the two numbers are gathered differently.


async def _finalize_usage(sink: dict) -> None:
    """Fill in a streamed call's dollar cost before it is merged.

    Streaming responses carry tokens but not cost, so this reads the real figure
    back from OpenRouter using the generation id. Non-streaming calls already
    have `cost_usd` and skip the request entirely.
    """
    if sink.get("cost_usd") is not None:
        return
    sink["cost_usd"] = await fetch_generation_cost(sink.get("generation_id"))


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


def _to_langchain_messages(
    messages: list[ChatMessage], system: str
) -> list[BaseMessage]:
    """Render the conversation for a chat model call.

    Anything that is not already an assistant turn is sent as a user turn, which
    is what the previous OpenAI-shaped call did — the pipeline only ever stores
    'user' and 'assistant', and a third role reaching here would be a bug
    upstream rather than something to represent faithfully.
    """
    out: list[BaseMessage] = [SystemMessage(content=system)]
    for msg in messages:
        if msg.role == "assistant":
            out.append(AIMessage(content=msg.content))
        else:
            out.append(HumanMessage(content=msg.content))
    return out


async def _stream_text(
    model: BaseChatModel,
    messages: list[BaseMessage],
    usage_sink: dict | None,
) -> AsyncIterator[str]:
    """Stream text from a chat model, capturing usage as it goes.

    Usage arrives on its own trailing chunk with empty content, so accumulating
    across every chunk (rather than reading the last one) is what makes both the
    text and the numbers come out right.
    """
    aggregate: Any = None
    async for chunk in model.astream(messages):
        # Summed rather than tracked separately: adding chunks is how LangChain
        # merges content *and* metadata, so the usage-only trailing chunk lands
        # on the aggregate without needing to be recognised as it goes past.
        aggregate = chunk if aggregate is None else aggregate + chunk
        if text := chunk.text:
            yield text

    if aggregate is not None:
        usage = usage_from_message(aggregate)
        _warn_if_runaway(model, usage)
        if usage_sink is not None:
            usage_sink.update(usage)


def _warn_if_runaway(model: BaseChatModel, usage: dict[str, Any]) -> None:
    """Log a streamed prose call that was cut off by its own token cap.

    THIS IS A DEGENERATION SIGNAL, NOT A BUDGET COMPLAINT. Both callers of
    `_stream_text` ask for something short — the elicitation prompt says "under
    80 words", the recommendation prompt "under 50 words" — against caps of 256
    and 128. Finishing on `length` therefore means the model did not stop when
    it should have, which in production has surfaced as a single token repeated
    until the cap cut it off. Raising the cap would only make that longer.
    Anything logged here is worth correlating with the provider: a runaway is
    usually a property of the upstream that served the call rather than of the
    model slug, and `provider` is recorded for exactly that reason.

    Only the streaming path is checked. The router legitimately runs against an
    8-token cap to return a single integer, so `length` is unremarkable there
    and warning on it would be pure noise.

    RESOLVING THE UPSTREAM. The generation id is in the message because the
    thing worth correlating across occurrences is which provider OpenRouter
    routed to, and that is not in the response — see the note in
    app/services/llm/openrouter.py. Read it off the generation record:

        curl -H "Authorization: Bearer $OPENROUTER_API_KEY" \
             "https://openrouter.ai/api/v1/generation?id=<generation_id>"

    and take `provider_name`. Allow ten seconds or so after the call; before
    that the record 404s. If one name keeps coming up, OPENROUTER_PROVIDER is
    the lever — see the note on it in app/core/config.py.
    """
    if usage.get("finish_reason") != "length":
        return
    logger.warning(
        "chat: %s hit its %s-token cap instead of stopping (generation=%s) — "
        "the reply was truncated mid-flow and may be degenerate. This is a "
        "runaway, not a budget to raise: resolve the generation id to a "
        "provider_name before changing anything",
        usage.get("model") or getattr(model, "model_name", "unknown"),
        getattr(model, "max_tokens", None),
        usage.get("generation_id") or "unknown",
    )


# --- Stage 1 — Extract BuildProfile -------------------------------------------

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
                from app.services.recommender.components.extractprofile import (
                    load_program,
                )
                from app.services.recommender.dspy_pipeline import configure_dspy

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

    # The langgraph import chain is a second multi-second cost on a cold start,
    # and it lands on whichever request happens to arrive first. Paid here
    # instead. Import only — compiling needs the event loop this runs beside,
    # and get_graph() caches on first use.
    import app.services.graph.graph  # noqa: F401


def _capture_dspy_usage(prediction: Any, usage_sink: dict) -> None:
    """Pull tokens + cost for the extractprofile DSPy call from the LM history."""
    try:
        import dspy

        from app.services.recommender.recording import extract_usage

        lm = dspy.settings.lm
        history_entry = dict(lm.history[-1]) if getattr(lm, "history", None) else None
        tokens_in, tokens_out, cost, model, _hash = extract_usage(
            prediction, history_entry
        )
        usage_sink.update(
            {
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost_usd": cost,
                "model": model,
            }
        )
    except Exception:
        logger.debug("failed to capture dspy usage for extractprofile", exc_info=True)


def _log_extraction_runaway(lm: Any) -> None:
    """Report a DSPy extraction whose response could not be parsed at all.

    WHY THIS IS NOT `_warn_if_runaway`. That one watches `_stream_text`, which
    only elicitation and recommendation use. Extraction does not go through it:
    it runs through DSPy, so a runaway there surfaces as an AdapterParseError
    rather than as a truncated stream, and no amount of instrumentation on the
    prose path would ever have seen it. This is the path that actually produced
    a wall of one repeated token in production.

    `finish_reason == "length"` distinguishes the two ways a parse can fail. The
    model filled its entire budget and was cut off — degeneration, retry it —
    versus it stopped on its own and simply did not emit the field markers the
    signature asked for, which is a prompt or model-capability problem that
    retrying will not fix. Both are logged; only the reason tells them apart.
    """
    finish_reason = None
    generation_id = None
    try:
        entry = lm.history[-1]
        response = entry.get("response")
        choice = response.choices[0]
        finish_reason = getattr(choice, "finish_reason", None)
        generation_id = getattr(response, "id", None)
    except Exception:  # noqa: BLE001 - diagnostics must not mask the parse error
        logger.debug("could not read finish_reason for a failed extraction")

    logger.warning(
        "chat: profile extraction produced an unparseable response "
        "(finish_reason=%s, generation=%s) — retrying once with the cache off. "
        "finish_reason='length' means the model ran to its cap instead of "
        "answering; resolve the generation id to a provider_name before "
        "concluding anything about the model itself",
        finish_reason or "unreported",
        generation_id or "unknown",
    )


def _format_conversation(messages: list[ChatMessage]) -> str:
    lines = []
    for msg in messages:
        prefix = "User" if msg.role == "user" else "Assistant"
        lines.append(f"{prefix}: {msg.content}")
    return "\n".join(lines)


# Unconstrained-budget statements, matched against the user's own turns to
# confirm a 'custom' budget_tier the extraction model proposed.
#
# Deliberately narrow. A false negative costs the user one clarifying exchange
# and an 'elite' build; a false positive silently removes every price ceiling
# from a build the user never said that about, and they find out at checkout.
# So bare "no budget" is NOT here — "I have no budget" far more often means
# "I have no money" than "I have unlimited money".
_UNLIMITED_BUDGET_PATTERNS = (
    r"\b(?:money|cost|price|budget)\s+(?:is|are)\s+no\s+(?:object|issue|concern)\b",
    r"\bno\s+(?:budget|price|spending|cost)\s+(?:limit|cap|ceiling|constraint)",
    r"\bbudget\s+is\s+(?:unlimited|limitless|open|uncapped)\b",
    r"\bunlimited\s+budget\b",
    r"\bspend\s+(?:whatever|as\s+much\s+as)\b",
    r"\bwhatever\s+it\s+(?:takes|costs)\b",
    r"\b(?:cost|price|money|budget)\s+(?:doesn'?t|does\s+not)\s+matter\b",
    r"\b(?:don'?t|do\s+not)\s+care\s+(?:about|what)\s+(?:the\s+)?(?:cost|price|money|it\s+costs)\b",
    r"\bregardless\s+of\s+(?:the\s+)?(?:cost|price)\b",
    r"\b(?:spare\s+no\s+expense|no\s+expense\s+spared)\b",
    # "the sky's the limit" and "the sky is the limit" — the possessive form
    # drops the verb, so the "is" has to be optional rather than required.
    r"\bsky(?:'?s|\s+is)\s+the\s+limit\b",
)

_UNLIMITED_BUDGET_RE = re.compile("|".join(_UNLIMITED_BUDGET_PATTERNS), re.IGNORECASE)


# Range a stated build budget has to fall in to be believed. Below the floor no
# complete machine exists in the catalog, and above the ceiling the figure is
# far more likely to be a parse artifact (a model number, a token count, cents
# read as dollars) than a budget anyone is spending. Out-of-range values fall
# back to the tier ladder rather than failing the turn — a wrong-by-1000x
# ceiling would sail past every candidate filter.
_STATED_BUDGET_MIN_USD = 300
_STATED_BUDGET_MAX_USD = 200_000


def _parse_stated_budget(raw: str | None) -> int | None:
    """The user's own dollar figure as an int, or None if absent/implausible.

    The extraction field asks for bare digits, but models reliably re-add the
    formatting they were told to drop ("$5,000"), so the symbols and separators
    are stripped here rather than trusted away.
    """
    if not raw:
        return None
    digits = re.sub(r"[^\d]", "", raw)
    if not digits:
        return None
    try:
        value = int(digits)
    except ValueError:
        return None
    if not (_STATED_BUDGET_MIN_USD <= value <= _STATED_BUDGET_MAX_USD):
        logger.info(
            "ignoring implausible stated_budget_usd %r from profile extraction; "
            "falling back to the budget tier ladder",
            raw,
        )
        return None
    return value


def _resolve_budget(
    budget_tier: str,
    price_sensitivity: str | None,
    messages: list[ChatMessage],
) -> tuple[str, str | None]:
    """Settle (budget_tier, price_sensitivity) together, before the gate sees them.

    Accept a 'custom' budget tier only if the user actually said so. 'custom'
    removes every price ceiling in the pipeline, which makes it the one tier
    where a hallucination is expensive rather than merely wrong — so it needs
    two independent signals to agree, not one. The extraction model proposes it
    from the conversation as a whole; this checks the user's own turns for an
    explicit statement, deterministically. An unconfirmed 'custom' falls back to
    'elite', the most permissive real tier, so the user still gets a high-end
    build and can restate their intent.

    Only user turns are scanned. The assistant saying "so, unlimited budget?"
    is the model's own words coming back around, and letting that confirm the
    tier would defeat the point of a second signal.

    THE DOWNGRADE ALSO HAS TO SUPPLY A price_sensitivity, and that is why this
    settles both fields rather than the tier alone. The two are read by
    different rules that used to deadlock against each other here: a downgraded
    'elite' is a known tier, so is_profile_complete demands a sensitivity, and
    the only way to get one is to ask whether the budget is a hard ceiling — the
    one question _ELICIT_SYSTEM forbids asking someone who has just said cost is
    no object. The elicitation model obeyed the prompt, declared it had
    everything it needed, and the turn routed back to `ask` forever without ever
    reaching the build pipeline. 'stretch' is the honest reading of a user whose
    budget talk was permissive enough for the extractor to call it unlimited:
    they will go higher for the right part. It only ever fills an absence — an
    explicit sensitivity from the extractor still wins.
    """
    if budget_tier != "custom":
        return budget_tier, price_sensitivity

    for msg in messages:
        if msg.role == "user" and _UNLIMITED_BUDGET_RE.search(msg.content or ""):
            return "custom", price_sensitivity

    logger.info(
        "profile extraction proposed budget_tier='custom' with no explicit "
        "user statement to back it; falling back to 'elite' with a 'stretch' "
        "price sensitivity"
    )
    return "elite", price_sensitivity or "stretch"


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
    from dspy.utils.exceptions import AdapterParseError

    from app.services.recommender.dspy_pipeline import session_lm

    conversation = _format_conversation(messages)
    program = _get_extract_program()
    lm = session_lm(session_id)
    with dspy.context(lm=lm):
        try:
            result = await asyncio.to_thread(program, conversation=conversation)
        except AdapterParseError:
            _log_extraction_runaway(lm)
            # RETRY WITH THE CACHE OFF, and the flag is the whole point.
            # dspy.LM caches on by default, and the retry sends byte-identical
            # inputs — so a plain second attempt is served the same unparseable
            # response out of the cache and fails exactly the same way, for
            # free, forever. `copy` leaves the configured LM untouched.
            #
            # One retry, not a loop. Degeneration is a sampling accident and a
            # fresh sample almost always parses; a model that has genuinely
            # stopped honouring the signature will not be fixed by asking
            # again, and the second failure is worth surfacing as one.
            with dspy.context(lm=lm.copy(cache=False)):
                result = await asyncio.to_thread(program, conversation=conversation)

    if usage_sink is not None:
        _capture_dspy_usage(result, usage_sink)

    games = [g.strip() for g in result.games.split(",") if g.strip()]
    workloads = [w.strip() for w in result.workloads.split(",") if w.strip()]

    def _opt(value: str) -> str | None:
        """Map the extraction model's 'none'/empty sentinel to a real None."""
        v = (value or "").strip()
        return None if not v or v.lower() == "none" else v

    def _pref(value: str) -> str | None:
        """Same as _opt, but 'no_preference' is also an absence of an answer.

        form_factor's sentinel differs from the others because it feeds
        UserPreferences, where "no_preference" is the real literal — see
        _effective_form_factor in dspy_pipeline for why that distinction has
        teeth (defaulting it to 'atx' silently narrows every motherboard query).
        """
        v = _opt(value)
        return None if v is None or v.lower() == "no_preference" else v

    budget_tier, price_sensitivity = _resolve_budget(
        result.budget_tier, _opt(getattr(result, "price_sensitivity", "")), messages
    )

    return BuildProfile(
        budget_tier=budget_tier,
        stated_budget_usd=_parse_stated_budget(
            _opt(getattr(result, "stated_budget_usd", ""))
        ),
        price_sensitivity=price_sensitivity,
        form_factor=_pref(getattr(result, "form_factor", "")),
        color_theme=_opt(getattr(result, "color_theme", "")),
        rgb_lighting=_opt(getattr(result, "rgb_lighting", "")),
        noise_tolerance=_opt(getattr(result, "noise_tolerance", "")),
        primary_use=result.primary_use,
        gaming_resolution=_opt(result.gaming_resolution),
        gaming_fps=_opt(result.gaming_fps),
        streaming_style=_opt(result.streaming_style),
        ai_workload=_opt(result.ai_workload),
        ai_model_scale=_opt(result.ai_model_scale),
        llm_quantization=_opt(getattr(result, "llm_quantization", "")),
        llm_context_tokens=_opt(getattr(result, "llm_context_tokens", "")),
        server_workload=_opt(result.server_workload),
        server_gpu_count=_opt(result.server_gpu_count),
        editing_resolution=_opt(result.editing_resolution),
        rendering_software=_opt(result.rendering_software),
        workload_intensity=_opt(result.workload_intensity),
        games=games,
        workloads=workloads,
        notes=result.notes,
    )


# --- Stage 2 — Stream Recommendation ------------------------------------------

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
    if profile.server_workload:
        bits.append(f"server workload: {profile.server_workload}")
    if profile.server_gpu_count:
        bits.append(f"GPU count: {profile.server_gpu_count}")
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

    def _line(p: dict) -> str:
        # "2x " rather than a silent single entry: without it the lead-in would
        # describe a four-GPU server as though it had one card.
        quantity = p.get("quantity") or 1
        prefix = f"{quantity}x " if quantity > 1 else ""
        line = f"  - {p['component']}: {prefix}{p['brand']} {p['model']}"
        if p.get("approx_price") is None:
            return line
        # approx_price is per unit; show what the line actually costs.
        return f"{line} (~${p['approx_price'] * quantity / 100:.0f})"

    parts_text = "\n".join(_line(p) for p in build["parts"])
    return f"""\
USER PROFILE:
  Primary use: {profile.primary_use}
  Use-case details: {_profile_details(profile)}
  Budget tier: {"no limit stated by the user" if profile.budget_tier == "custom" else profile.budget_tier}
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

    If usage_sink is provided, it's filled with this call's tokens and the
    generation id its dollar cost is read back from. session_id groups this call
    with the rest of the turn's calls in OpenRouter's dashboard.
    """
    model = get_chat_model(
        ChatModelConfig.get_recommend_model(),
        session_id=session_id,
        temperature=0.5,
        max_tokens=ChatModelConfig.RECOMMEND_MAX_TOKENS,
        streaming=True,
    )

    api_messages = _to_langchain_messages(messages, _RECOMMEND_SYSTEM)
    api_messages.append(
        HumanMessage(content=_format_build_context(profile, build_key, build))
    )

    async for text in _stream_text(model, api_messages, usage_sink):
        yield text


# --- Conversational fallback (not enough info yet) ----------------------------

_ELICIT_SYSTEM = """\
You are Palladium's friendly intake assistant. Learn the user's needs to recommend a PC build.

Determine:
1. Primary use case (gaming, streaming, video editing, 3D rendering, AI/ML,
   a server/workstation, software development, music production, or general
   productivity)
2. For gaming (or streaming gameplay): target resolution AND target frame rate, plus game types
3. For streaming: whether they stream while gaming or camera/IRL content only
4. For AI/ML: the workload (running models, training/fine-tuning, image generation)
   and roughly how large the models are
5. For a server: what it will run (AI training, AI serving, HPC/simulation,
   virtualization or a homelab, storage, a render farm node) AND how many GPUs
   it has to host — the GPU count is what decides whether this needs a
   workstation platform like Threadripper rather than a desktop one
5b. For any build that runs or trains LLMs: whether they're willing to run
   models QUANTIZED, and what CONTEXT WINDOW they need. Ask these only after
   you know what they're running.
6. For video editing: the resolution of the footage they edit
7. For 3D rendering: which software/renderer they work in
8. For software development or music production: how heavy the workload is
   (codebase size, VMs/containers; track and plugin counts)
9. Budget expectations (even vague is fine)
10. Once a budget is known, whether it is a hard ceiling or they'd stretch a
   little for the right part

On budget, take the user at their word in both directions. A vague answer is
enough — don't push for a figure. If they say there is no limit, accept that and
move on; don't talk them into naming one. But never put "unlimited" in their
mouth either: if they haven't said it, don't offer it as an option.

Item 10 is about how firm the number is, not how big — ask it only after they
have given one, and never of someone who has said cost is no object.

ON QUANTIZATION (item 5b), you are expected to teach, not just collect. Most
people running a model locally have no idea what it means, and the question
decides whether they need an $800 card or a $15,000 one — so it is worth a
sentence of explanation rather than a bare ask. Quantization compresses a
model's weights so it needs far less VRAM: a 31B model is roughly 74GB at full
precision and roughly 19GB at 4-bit, which is the difference between a
workstation card and a mainstream one. The quality cost is small and most
people self-hosting run 4-bit or 8-bit as a matter of course.

If they ask what the levels mean, explain plainly: higher-bit formats (q8, fp8)
are closer to the original model and need about twice the memory of 4-bit;
4-bit (q4) is the usual sweet spot; 3-bit and below start to degrade
noticeably. Give them the tradeoff and let them choose.

"I don't know" is a perfectly good answer to item 5b — accept it, say you'll
assume a sensible default, and move on. Never make someone feel they should
have known.

On context window: ask it in plain terms — how much text the model needs to
consider at once (a short chat, a long document, a whole codebase). Long context
costs real VRAM on top of the model itself, which is why it is worth asking.

Ask ONE focused follow-up question at a time. Be conversational. Keep responses under 80 words.

Do not describe or recommend a build yourself. Do not say the build is ready, that you have
enough information, or that a recommendation is coming — the handoff to the build recommender
happens automatically and silently once every required item is filled in; you will be told
exactly what's still missing below, so just ask about that.
"""


def _is_llm_build(profile: BuildProfile) -> bool:
    """Whether this build exists to run or train language models.

    The quantization and context-window questions are only coherent for these —
    asking someone building a Stable Diffusion box what context length they
    want is noise. image_gen is deliberately excluded: diffusion models do
    quantize, but their VRAM is dominated by resolution and batch rather than
    by weights plus KV cache, so the same two questions would not size it.
    """
    if profile.primary_use == "ai":
        return profile.ai_workload in ("inference", "training")
    if profile.primary_use == "server":
        return profile.server_workload in ("ai_serving", "ai_training")
    return False


def _missing_llm_serving_fields(profile: BuildProfile) -> list[str]:
    """The LLM-shape gaps, in the order they make sense to ask about.

    Deliberately empty until the workload itself is known — these are follow-ups
    to "you're running LLMs", not opening questions, and the router picking
    "what context window?" before the user has said what they want to run would
    be incoherent.
    """
    if not _is_llm_build(profile):
        return []
    missing: list[str] = []
    if profile.llm_quantization is None:
        missing.append(
            "whether they're willing to run models quantized (compressed weights) "
            "or need full precision — this is the single biggest factor in how "
            "much GPU the build needs"
        )
    if profile.llm_context_tokens is None:
        missing.append("how much context they need the model to handle at once")
    return missing


# Mirrors is_profile_complete()'s branches so the elicitation model is told
# exactly which field it's blocked on, instead of judging "enough info" for
# itself from the raw conversation (which is what let it drift into saying
# things like "the build recommender will be back with your build").
def _missing_fields(profile: BuildProfile) -> list[str]:
    if profile.primary_use == "unknown":
        return [
            "their primary use case (gaming, streaming, video editing, "
            "3D rendering, AI/ML, a server or workstation, software development, "
            "music production, or general productivity)"
        ]

    missing: list[str] = []
    use = profile.primary_use
    if use == "gaming":
        if profile.gaming_resolution is None:
            missing.append("target gaming resolution")
        if profile.gaming_fps is None:
            missing.append("target frame rate")
    elif use == "streaming":
        if profile.streaming_style is None:
            missing.append(
                "whether they stream while gaming or camera/IRL content only"
            )
        elif profile.streaming_style == "while_gaming":
            if profile.gaming_resolution is None:
                missing.append("target gaming resolution")
            if profile.gaming_fps is None:
                missing.append("target frame rate")
    elif use == "ai":
        if profile.ai_workload is None:
            missing.append(
                "the AI workload (running models, training/fine-tuning, or image generation)"
            )
        elif (
            profile.ai_workload in ("inference", "training")
            and profile.ai_model_scale is None
        ):
            missing.append("roughly how large the models are")
        else:
            missing.extend(_missing_llm_serving_fields(profile))
    elif use == "server":
        if profile.server_workload is None:
            missing.append(
                "what the server will run (AI training, AI serving, HPC/simulation, "
                "virtualization or a homelab, storage, or a render farm node)"
            )
        # Asked for every workload, including the CPU-only ones: "zero GPUs" is
        # the answer that sends the build toward cores and memory bandwidth
        # instead of PCIe lanes, so it is as load-bearing as "eight".
        if profile.server_gpu_count is None:
            missing.append("how many GPUs the server needs to host")
        missing.extend(_missing_llm_serving_fields(profile))
    elif use == "video_editing":
        if profile.editing_resolution is None:
            missing.append("the resolution of the footage they edit")
    elif use == "3d_rendering":
        if profile.rendering_software is None:
            missing.append("which 3D software/renderer they work in")
    elif use in ("software_dev", "music_production"):
        if profile.workload_intensity is None:
            missing.append(
                "how heavy the workload is (codebase size/VMs, or track/plugin counts)"
            )

    if profile.budget_tier == "unknown":
        missing.append("budget expectations")
    elif profile.budget_tier != "custom" and profile.price_sensitivity is None:
        missing.append(
            "whether that budget is a hard ceiling or they'd stretch a little "
            "for the right part"
        )

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

    If usage_sink is provided, it's filled with this call's tokens and the
    generation id its dollar cost is read back from. session_id groups this call
    with the rest of the turn's calls in OpenRouter's dashboard.
    """
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

    model = get_chat_model(
        ChatModelConfig.get_elicit_model(),
        session_id=session_id,
        temperature=0.6,
        max_tokens=ChatModelConfig.ELICIT_MAX_TOKENS,
        streaming=True,
    )

    async for text in _stream_text(
        model, _to_langchain_messages(messages, system_content), usage_sink
    ):
        yield text


# Checks if the extracted profile actually carries enough signal to recommend.
# This is a hard, code-level decision over structured fields the model
# populates — the model never decides readiness itself.


def is_profile_complete(profile: BuildProfile) -> bool:
    """
    A profile is complete once primary_use and budget_tier have both been
    inferred, plus the use-case-specific fields that meaningfully fork the
    build: gaming needs resolution AND target frame rate; streaming needs a
    style (and resolution + frame rate when streaming while gaming); AI needs
    a workload (and a model scale for LLM workloads); a server needs a workload
    AND a GPU count; video editing needs footage resolution; 3D rendering needs
    the software used; software dev and music production need a workload
    intensity.

    A known budget also needs a price_sensitivity, because the tier alone does
    not say where in its band to aim (see _budget_for). The exception is the
    'custom' tier, where there is no figure for sensitivity to qualify — asking
    a user who just said cost is no object whether that is a hard ceiling would
    be answering a question they already answered.

    The stated-preference fields (form_factor, color_theme, rgb_lighting,
    noise_tolerance) are deliberately absent from this check. They improve a
    build when volunteered but none of them forks it, and gating on taste would
    turn a two-question intake into a survey.
    """
    if profile.primary_use == "unknown":
        return False
    if profile.budget_tier == "unknown":
        return False
    if profile.budget_tier != "custom" and profile.price_sensitivity is None:
        return False

    use = profile.primary_use
    if use == "gaming":
        return profile.gaming_resolution is not None and profile.gaming_fps is not None
    if use == "streaming":
        if profile.streaming_style is None:
            return False
        if profile.streaming_style == "while_gaming":
            return (
                profile.gaming_resolution is not None and profile.gaming_fps is not None
            )
        return True
    if use == "ai":
        if profile.ai_workload is None:
            return False
        if (
            profile.ai_workload in ("inference", "training")
            and profile.ai_model_scale is None
        ):
            return False
        return not _missing_llm_serving_fields(profile)
    if use == "server":
        if profile.server_workload is None or profile.server_gpu_count is None:
            return False
        return not _missing_llm_serving_fields(profile)
    if use == "video_editing":
        return profile.editing_resolution is not None
    if use == "3d_rendering":
        return profile.rendering_software is not None
    if use in ("software_dev", "music_production"):
        return profile.workload_intensity is not None
    return True


# --- Build resolution cache — profile → build mapping is deterministic --------

_resolve_cache: dict[str, tuple[str, Build]] = {}


async def _resolve_build_cached(
    profile: BuildProfile,
    db,
) -> tuple[str, Build]:
    """Resolve a build with in-memory caching to avoid repeated DB queries."""
    cache_key = (
        f"{profile.primary_use}:{profile.gaming_resolution}:{profile.budget_tier}"
    )
    if cache_key in _resolve_cache:
        return _resolve_cache[cache_key]

    build_key, build = await resolve_build(profile, db)
    _resolve_cache[cache_key] = (build_key, build)
    return build_key, build


# --- DSPy custom-build path ---------------------------------------------------
# Once the profile is complete, the DSPy pipeline and the reference-build
# resolver are started together. The reference build is the guaranteed
# fallback: if the DSPy run errors out or exceeds the timeout, its result is
# thrown away and the reference build is what gets recommended.

_DSPY_CHAT_TIMEOUT_S = float(os.getenv("DSPY_CHAT_TIMEOUT_S", "180"))

# Sentinel the DSPy runner always enqueues last, so the progress-forwarding
# loop in run_chat_turn knows the pipeline is finished (success or not).
_PIPELINE_DONE = object()

_BUDGET_TIER_USD = {"entry": 1000, "mid": 1500, "high": 2500, "elite": 4000}

# Server builds start where a consumer "elite" build ends: a Threadripper and a
# TRX50 board alone are past $2000 before any GPU, so mapping a server profile
# onto the desktop ladder would hand every step a ceiling no server part clears
# and fail the run at the CPU query. Same tier labels, re-scaled.
_SERVER_BUDGET_TIER_USD = {"entry": 4000, "mid": 7000, "high": 12000, "elite": 25000}


# How far the tier's headline figure moves with the user's own framing of it.
# A tier is a band, and these pick where in that band to aim: someone who called
# their number an absolute ceiling should not be shown a build that sits on it,
# and someone who said they'd go higher for the right part should not be capped
# at a figure they already disowned.
#
# Deliberately narrow (±10-15%). These shift which parts clear the filter at the
# margins; they are not a licence to rewrite the budget the user gave.
_PRICE_SENSITIVITY_SCALE = {"firm": 0.90, "flexible": 1.00, "stretch": 1.15}


# Bounds on how far the measured market drift may move the ladder. Part prices
# swing hard, but a ladder half or triple its tuned level is far more likely to
# mean a broken catalog (a pricing ETL that wrote cents as dollars, a batch of
# parts deactivated) than a real market. Clamped rather than rejected: a genuine
# 2.5x market still gets the full 2.5x, it just cannot go further unattended.
_DRIFT_MIN, _DRIFT_MAX = 0.5, 2.5

# Drift moves on the timescale of a pricing ETL run, not a chat turn, so it is
# measured once and reused. Process-local and time-based: a stale factor for a
# few minutes is harmless, and the alternative is this query on every build.
_DRIFT_TTL_S = 900.0
_drift_cache: tuple[float, float] | None = None  # (factor, measured_at)


async def _market_drift(db) -> float:
    """The cached market drift factor, or 1.0 when it cannot be measured.

    1.0 is the honest fallback: it means "spend the ladder as written", which is
    the behaviour that existed before drift was measured at all. This must never
    fail a build — a budget is not worth an exception.
    """
    global _drift_cache

    now = time.monotonic()
    if _drift_cache is not None and now - _drift_cache[1] < _DRIFT_TTL_S:
        return _drift_cache[0]

    try:
        from app.crud.reference_builds import market_drift_factor

        measured = await market_drift_factor(db)
    except Exception:
        logger.warning(
            "market drift measurement failed; using ladder as written", exc_info=True
        )
        measured = None

    factor = 1.0 if measured is None else max(_DRIFT_MIN, min(measured, _DRIFT_MAX))
    if measured is not None and factor != measured:
        logger.warning(
            "market drift %.3f outside [%.1f, %.1f]; clamped to %.3f — check the "
            "parts catalog and pricing ETL",
            measured,
            _DRIFT_MIN,
            _DRIFT_MAX,
            factor,
        )
    _drift_cache = (factor, now)
    return factor


async def _budget_for_async(profile: BuildProfile, db) -> int:
    """_budget_for, with the tier ladder scaled to current catalog prices.

    Only the ladder is scaled. A stated figure is the user's own money and means
    the same thing whatever the market did; 'custom' has no figure to scale. So
    drift applies to exactly the case it was built for — the user who never named
    a number and is being sized by constants somebody typed months ago.
    """
    if profile.budget_tier == "custom" or profile.stated_budget_usd is not None:
        return _budget_for(profile)

    tiers = (
        _SERVER_BUDGET_TIER_USD if profile.primary_use == "server" else _BUDGET_TIER_USD
    )
    base = tiers.get(profile.budget_tier, tiers["mid"])
    scale = _PRICE_SENSITIVITY_SCALE.get(profile.price_sensitivity or "", 1.0)
    return int(base * scale * await _market_drift(db))


def _budget_for(profile: BuildProfile) -> int:
    """Total build budget in USD for a profile, or NO_BUDGET_CEILING.

    'custom' short-circuits everything below: it is not a bigger tier, it is the
    absence of one. By the time a profile reaches here that tier has already
    been confirmed against the user's own words (_confirm_custom_budget), so
    this can take it at face value — and price_sensitivity cannot apply to it,
    because there is no figure to scale.

    A STATED FIGURE ALWAYS WINS OVER THE LADDER, and it has to. The tiers are
    coarse buckets mapped to one constant each, so routing a stated number
    through them destroys it: $2200 and $1500 are both 'mid' and both came back
    out as $1500. Worse, the ladder is picked by use case, and the server ladder
    is a different scale entirely — a user who said $5000 and the word "server"
    was handed $25000 and shown a $15000 GPU, while the chat text still quoted
    them their own $5000. The ladder's job is to answer "roughly what should
    this cost?" for someone who never named a number, not to overrule someone
    who did.
    """
    if profile.budget_tier == "custom":
        return NO_BUDGET_CEILING
    scale = _PRICE_SENSITIVITY_SCALE.get(profile.price_sensitivity or "", 1.0)
    if profile.stated_budget_usd is not None:
        return int(profile.stated_budget_usd * scale)
    tiers = (
        _SERVER_BUDGET_TIER_USD if profile.primary_use == "server" else _BUDGET_TIER_USD
    )
    base = tiers.get(profile.budget_tier, tiers["mid"])
    return int(base * scale)


# BuildProfile.primary_use → BuildRequest use-case key (must match the keys
# _allocate_budget knows: gaming, streaming, creator, rendering, aiml, server,
# dev, audio, default-anything-else).
_PRIMARY_USE_TO_USE_CASE = {
    "gaming": "gaming",
    "streaming": "streaming",
    "video_editing": "creator",
    "3d_rendering": "rendering",
    "ai": "aiml",
    "server": "server",
    "software_dev": "dev",
    "music_production": "audio",
    "general": "productivity",
}

# Display order + labels for the assembled BuildCard parts list.
#
# Each entry is (label, state attribute, quantity attribute). The attribute
# holds either one name or a list of them, and the quantity attribute — None
# for the single-instance roles — says how many of each. The two shapes mirror
# BuildPart: distinct parts in a role are separate rows (storage), identical
# ones are one row with a quantity (GPUs, fans).
_DSPY_COMPONENT_SLOTS: list[tuple[str, str, str | None]] = [
    ("CPU", "cpu_name", None),
    ("CPU Cooler", "cooler_name", None),
    ("Motherboard", "mobo_name", None),
    ("RAM", "ram_name", None),
    ("Storage", "storage_names", None),
    ("GPU", "gpu_name", "gpu_count"),
    ("PSU", "psu_name", None),
    ("Case", "case_name", None),
    ("Case Fans", "fans_name", "fans_quantity"),
]


def _profile_to_preferences(profile: BuildProfile) -> UserPreferences:
    """Map the stated-preference half of a chat profile onto UserPreferences.

    Only fields the user actually volunteered are set; everything else keeps
    UserPreferences' own defaults, which are the "no preference" values the
    pipeline is built around. Notably form_factor: leaving it at
    "no_preference" is what keeps _effective_form_factor from narrowing the
    motherboard query to ATX for a user who never mentioned a case.
    """
    prefs = UserPreferences()
    if profile.form_factor:
        prefs.form_factor = profile.form_factor  # type: ignore[assignment]
    if profile.color_theme:
        prefs.color_theme = profile.color_theme
    if profile.rgb_lighting == "yes":
        prefs.rgb_lighting = True
    return prefs


# The user's answer, translated into what the build steps should actually do.
#
# 'unsure' resolving to 4-bit is the load-bearing entry: it is what makes "I
# don't know" a safe answer instead of a blocking one, and 4-bit is what people
# self-hosting a model overwhelmingly run. Sizing an unsure user for fp16 would
# reintroduce the original failure by way of politeness.
_QUANTIZATION_GUIDANCE = {
    "yes": (
        "willing to run quantized — size VRAM for 4-bit (q4) weights, "
        "roughly 0.5 bytes per parameter"
    ),
    "no": (
        "wants FULL PRECISION — size VRAM for fp16/bf16 weights, 2 bytes per "
        "parameter, and do not assume quantization to make a smaller card fit"
    ),
    "unsure": (
        "no preference stated on quantization — assume 4-bit (q4), the format "
        "self-hosters normally run, and size VRAM at roughly 0.5 bytes per "
        "parameter"
    ),
}

# Context length in tokens, and what it costs. KV cache scales with context and
# is charged on top of the weights, which is why a model that "fits in 24GB" at
# 8k may not at 128k.
_CONTEXT_GUIDANCE = {
    "4k": "short context (~4k tokens) — KV cache overhead is minimal",
    "8k": "standard context (~8k tokens) — modest KV cache overhead",
    "32k": (
        "long context (~32k tokens) — budget meaningful extra VRAM for KV cache "
        "on top of the weights"
    ),
    "128k": (
        "very long context (~128k tokens) — KV cache can rival the weights "
        "themselves; prefer a card with clear VRAM headroom over one that only "
        "just fits the model"
    ),
    "unsure": "no context length stated — assume a standard ~8k window",
}


def _profile_to_build_request(
    profile: BuildProfile, budget_usd: int | None = None
) -> BuildRequest:
    """Map the chat-extracted BuildProfile onto the pipeline's BuildRequest.

    budget_usd overrides the synchronous ladder when the caller has already
    resolved a drift-scaled figure against a DB session (see _run_dspy_build).
    Left optional so the sync callers and tests that only need the constants
    keep working unchanged.
    """
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
    # Rendered as an instruction rather than as the raw enum. These land in the
    # `use_cases` string every Decide* step reads, and "llm.quantization: unsure"
    # tells the GPU step nothing it can act on — "size for 4-bit" does. The
    # translation is here rather than in the prompt because the default for
    # 'unsure' is a product decision, not something to re-litigate per call.
    # .get, not [] — these values come from a language model, and an off-menu
    # string must degrade to the default rather than take the build down.
    if profile.llm_quantization:
        answers["llm.quantization"] = _QUANTIZATION_GUIDANCE.get(
            profile.llm_quantization, _QUANTIZATION_GUIDANCE["unsure"]
        )
    if profile.llm_context_tokens:
        answers["llm.context"] = _CONTEXT_GUIDANCE.get(
            profile.llm_context_tokens, _CONTEXT_GUIDANCE["unsure"]
        )
    if profile.server_workload:
        answers["server.workload"] = profile.server_workload
    if profile.server_gpu_count:
        answers["server.gpu_count"] = profile.server_gpu_count
    if profile.editing_resolution:
        answers["editing.footage_resolution"] = profile.editing_resolution
    if profile.rendering_software:
        answers["rendering.software"] = profile.rendering_software
    if profile.workload_intensity:
        answers["general.workload_intensity"] = profile.workload_intensity
    # Both also reach the Decide* modules as free text via _request_summary's
    # Q&A flattening. price_sensitivity is already applied numerically in
    # _budget_for; saying it in words too lets a step tell "$1350 of a firm
    # $1500" apart from "$1350 of a flexible $1350", which the number alone
    # cannot express. noise_tolerance has no numeric analogue at all — the
    # cooler and fan steps are the only place it can land.
    if profile.price_sensitivity:
        answers["general.price_sensitivity"] = profile.price_sensitivity
    if profile.noise_tolerance:
        answers["general.noise_tolerance"] = profile.noise_tolerance
    if profile.games:
        answers["gaming.games"] = profile.games
    if profile.workloads:
        answers["general.workloads"] = profile.workloads
    if profile.notes:
        answers["general.notes"] = profile.notes
    return BuildRequest(
        use_cases=[_PRIMARY_USE_TO_USE_CASE.get(profile.primary_use, "productivity")],
        budget_usd=_budget_for(profile) if budget_usd is None else budget_usd,
        preferences=_profile_to_preferences(profile),
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

    resolved: list[tuple[str, str, Any, int | None, int]] = []
    total_cents = 0
    for component, attr, quantity_attr in _DSPY_COMPONENT_SLOTS:
        value = getattr(state, attr, None)
        # One attribute, two shapes: a list for roles whose members differ
        # (storage), a bare name for everything else.
        names = [n for n in (value if isinstance(value, list) else [value]) if n]
        quantity = max(1, getattr(state, quantity_attr, 1) or 1) if quantity_attr else 1
        for name in names:
            part = await get_part_by_name(db, name)
            # Grouped parts (GPU/PSU/RAM/Storage) carry price on their group, not
            # the exact pc_parts row — resolve_part_price_cents handles both.
            price_cents = (
                await resolve_part_price_cents(db, part) if part is not None else None
            )
            if price_cents is not None:
                # Per-unit price times the count, matching BuildPart's
                # line_total_cents. Summing the unit price would under-report a
                # four-GPU build by three cards.
                total_cents += price_cents * quantity
            resolved.append((component, name, part, price_cents, quantity))

    amazon_urls = await get_amazon_urls_by_part(
        db, [part.id for _, _, part, _, _ in resolved if part is not None]
    )

    parts = [
        {
            "component": component,
            "brand": (part.manufacturer if part else None) or "",
            "model": name,
            # Per unit, like pc_build_parts.price_at_build — the card multiplies
            # by quantity for display rather than being handed a line total it
            # can't decompose.
            "approx_price": price_cents,
            "quantity": quantity,
            "part_id": str(part.id) if part is not None else "",
            "amazon_url": amazon_urls.get(part.id) if part is not None else None,
        }
        for component, name, part, price_cents, quantity in resolved
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


async def _resolve_drift_scaled_budget(profile: BuildProfile) -> int:
    """The build budget, with the ladder scaled to current catalog prices.

    Takes its own short session rather than reusing the pipeline's, so the
    request (and the recorder built from it) can still be constructed before the
    try block that owns the pipeline session — the cancellation handler there
    reads `recorder`, so it has to exist first. The drift factor is cached for
    _DRIFT_TTL_S, so in the steady state this opens a session and runs no query.

    Never raises: a budget is not worth failing a build over, and the
    synchronous ladder is always a valid answer.
    """
    try:
        async with AsyncSessionLocal() as db:
            return await _budget_for_async(profile, db)
    except Exception:
        logger.warning(
            "drift-scaled budget lookup failed; using the ladder as written",
            exc_info=True,
        )
        return _budget_for(profile)


async def _enrich_case_options(options: list[dict], db) -> list[dict]:
    """Resolve the 3 chosen case names against the catalog for the picker cards.

    The pipeline's options carry only {name, reason} — what the LLM decided.
    The cards additionally need identity (part_id), the street price, the size
    and the image with its attribution, all of which live on the Case row. A
    name the catalog can't resolve (the model does occasionally paraphrase)
    still yields a card: name and reason are the load-bearing fields, and the
    picker renders missing images and prices gracefully.
    """
    from app.crud.components import get_case_by_name

    enriched: list[dict] = []
    for option in options:
        name = option.get("name") or ""
        case = await get_case_by_name(db, name) if name else None
        enriched.append(
            {
                "name": name,
                "reason": option.get("reason") or "",
                "part_id": str(case.id) if case is not None else "",
                "approx_price": case.street_price_cents if case is not None else None,
                "size": case.size if case is not None else None,
                "image_url": case.image_url if case is not None else None,
                "image_credit": case.image_credit if case is not None else None,
                "image_source_url": (
                    case.image_source_url if case is not None else None
                ),
            }
        )
    return enriched


def resolve_case_choice(submitted: str | None, options: list[dict]) -> str:
    """Which case the build actually uses, given what came back with the pick.

    A name outside the offered three is refused and the best-value option used
    instead. The endpoint that accepts picks cannot know which three cases a
    given build proposed — the options live in the paused payload, not in the
    request — so validating happens here, against what was actually generated.
    Anything else is a name the picker UI could not have produced (a stale
    token replayed against a newer build, a hand-crafted request), and obeying
    it would let a caller steer a build into a case the pipeline never checked
    for fit.

    `submitted` is None only when a resume arrives with no name at all, which
    the same fallback covers.
    """
    valid = {o["name"] for o in options if o.get("name")}
    if submitted in valid:
        return submitted  # type: ignore[return-value]
    if submitted is not None:
        logger.warning("case choice %r not among offered options", submitted)
    return options[0]["name"]


# Marks a build that stopped at the case step rather than finishing. Returned
# by _run_dspy_build in place of a payload, so the `build` node can tell "the
# pipeline is waiting on the user" apart from "the pipeline failed" — the
# first ends the turn with a picker, the second falls back to the reference
# build.
class PausedAtCase:
    """The pipeline stopped to ask for a case. Carries what the turn still
    has to emit: the options to show, and the token that resumes them."""

    __slots__ = ("token", "options")

    def __init__(self, token: str, options: list[dict]) -> None:
        self.token = token
        self.options = options


async def _run_dspy_build(
    profile: BuildProfile,
    progress_queue: asyncio.Queue,
    ref_task: asyncio.Task,
    conversation_id: str | None = None,
    messages: list[ChatMessage] | None = None,
) -> dict | PausedAtCase | None:
    """
    Run the DSPy pipeline as far as the case step, on its own DB session.

    Three outcomes, and the caller treats each differently:
      PausedAtCase  the pipeline reached the case step and saved itself. The
                    turn ends showing the picker; the user's pick resumes it.
      None          something failed. The caller falls back to the reference
                    build, exactly as before.
      dict          not produced here any more — a completed build now only
                    ever comes out of the resume (see resume_build below).

    Always enqueues _PIPELINE_DONE last. Never raises.

    ref_task is the reference build being resolved in parallel. It is recorded
    onto the same build_sessions row as the DSPy run via the recorder, which
    means it has to be awaited *before* the pause: the recorder is serialized
    into the paused payload, and a reference build attached after that snapshot
    would not survive into the resumed half.

    WHY THE PAUSE IS PERSISTED RATHER THAN WAITED OUT. Nine LLM-backed
    decisions have been made by this point, and the tenth needs a human. Waiting
    inline would hold a worker thread and a Pub/Sub lease for as long as the
    user takes, and still strand anyone who answered late. Writing the state to
    Valkey and Postgres instead (services/paused_build.py) makes the wait free
    and effectively unbounded — the pick simply picks the pipeline back up.
    """
    from app.models.build_session import BuildSessionStatus
    from app.services import paused_build
    from app.services.recommender.dspy_pipeline import PIPELINE_VERSION, run_pipeline
    from app.services.recommender.recording import BuildRecorder

    request = _profile_to_build_request(
        profile, budget_usd=await _resolve_drift_scaled_budget(profile)
    )
    recorder = BuildRecorder(request, PIPELINE_VERSION, conversation_id=conversation_id)

    def _progress(step: str, message: str) -> None:
        progress_queue.put_nowait(
            {"type": "progress", "step": step, "message": message}
        )

    try:
        async with AsyncSessionLocal() as db:
            state = await run_pipeline(
                request, db, progress_callback=_progress, recorder=recorder
            )
            if state.error:
                logger.warning(
                    "DSPy pipeline failed; using reference build: %s", state.error
                )
                return None
            if not state.case_options or not state.case_options[0].get("name"):
                logger.warning(
                    "DSPy pipeline produced no case options; using reference build"
                )
                recorder.finish(BuildSessionStatus.ERROR)
                return None

            options = await _enrich_case_options(state.case_options, db)
            # The pipeline session id doubles as the pick token: unique per
            # run, already minted, and meaningless outside this build.
            token = state.session_id or str(uuid.uuid4())

            # Awaited before the snapshot, not after — see the docstring.
            await _attach_reference_build(recorder, ref_task)

            saved = await paused_build.save(
                token,
                conversation_id,
                {
                    "state": state.to_dict(),
                    "recorder": recorder.to_dict(),
                    "profile": profile.model_dump(),
                    "options": options,
                    "conversation_id": conversation_id,
                    # The conversation as it read at the pause. The resume needs
                    # it to write the build's lead-in in context, and reading it
                    # back from Postgres instead would miss a guest's turns,
                    # which are never persisted.
                    "messages": [m.model_dump() for m in (messages or [])],
                },
            )
            if not saved:
                # Neither store accepted the pause, so no pick could ever be
                # acted on. Showing a picker here would be a lie; fall back to
                # the reference build like any other failure.
                logger.warning(
                    "paused build could not be persisted; using reference build"
                )
                recorder.finish(BuildSessionStatus.ERROR)
                return None

            return PausedAtCase(token=token, options=options)
    except asyncio.CancelledError:
        # Cancelled by run_chat_turn's deadline, or a worker shutting down.
        recorder.finish(BuildSessionStatus.ERROR)
        raise
    except Exception:
        logger.exception("DSPy pipeline crashed; using reference build")
        return None
    finally:
        progress_queue.put_nowait(_PIPELINE_DONE)


class ResumedBuild(NamedTuple):
    """What a resumed pipeline produced, for the turn that resumed it."""

    build: dict
    profile: BuildProfile
    messages: list[ChatMessage]
    case_options: dict
    session_id: str


async def resume_build(
    token: str,
    case_name: str,
    conversation_id: str | None = None,
    progress_callback: Callable[[str, str], None] | None = None,
) -> ResumedBuild | None:
    """Finish a build that stopped at the case step.

    Claims the paused payload (at most one caller wins, and only from the
    conversation that paused it — see paused_build.load_and_claim), restores
    the pipeline exactly as it stood, runs the remaining steps with the user's
    case, and assembles the build.

    Returns None when the pause cannot be claimed: an unknown token, a pick
    that lost the race with another pick, one aimed at a different
    conversation, or a payload that outlived both stores. The caller tells the
    user rather than silently building something else — every one of those
    means this particular build is gone, and quietly substituting a different
    one would show parts they never chose a case for.
    """
    from app.models.build_session import BuildSessionStatus
    from app.services import paused_build
    from app.services.recommender.dspy_pipeline import (
        DSPyBuildState,
        run_pipeline_post_case,
    )
    from app.services.recommender.recording import BuildRecorder

    payload = await paused_build.load_and_claim(token, conversation_id)
    if payload is None:
        logger.info("no claimable paused build for token %s", token)
        return None

    def _progress(step: str, message: str) -> None:
        if progress_callback is not None:
            progress_callback(step, message)

    recorder: Any = None
    try:
        state = DSPyBuildState.from_dict(payload["state"], progress_callback=_progress)
        recorder = BuildRecorder.restore(payload["recorder"])
        profile = BuildProfile(**payload["profile"])
        options = payload.get("options") or []
        messages = [ChatMessage(**m) for m in payload.get("messages") or []]

        # Validated against the options this build actually generated, which
        # only exist here — see resolve_case_choice.
        picked = resolve_case_choice(case_name, state.case_options)

        async with AsyncSessionLocal() as db:
            state = await run_pipeline_post_case(
                state, db, picked, recorder=recorder
            )
            if state.error:
                logger.warning("resumed post-case step failed: %s", state.error)
                return None
            build = await _assemble_dspy_build(state, db)

        return ResumedBuild(
            build=build,
            profile=profile,
            messages=messages,
            case_options={"token": token, "chosen": picked, "options": options},
            session_id=state.session_id or token,
        )
    except Exception:
        logger.exception("resuming a paused build crashed")
        if recorder is not None:
            recorder.finish(BuildSessionStatus.ERROR)
        return None


async def _load_cached_reference_build(
    conversation_id: str | None,
) -> tuple[str, Build] | None:
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
    if (
        conversation
        and conversation.reference_build_key
        and conversation.reference_build
    ):
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
        resolve_profile = profile.model_copy(
            update={"budget_tier": assumed_budget_tier}
        )

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


async def create_shared_build(
    payload: dict, build_key: str | None, conversation_id: str | None
) -> str | None:
    """Persist a public snapshot of this build and return its share token.

    Called from the build node for every emitted build — DSPy and reference
    alike — so the card can always offer a link and a PDF. The `profile` key is
    stripped from the stored copy: it paraphrases what the user told the intake
    chat, and a share link should republish the build, not the conversation.

    Returns None on any failure. A build is never worth failing over its share
    link; the card simply renders without the share actions.
    """
    from app.models.shared_build import SharedBuild

    snapshot = {k: v for k, v in payload.items() if k != "profile"}
    conv_uuid: uuid.UUID | None = None
    if conversation_id:
        try:
            conv_uuid = uuid.UUID(conversation_id)
        except ValueError:
            # Guest turns pass a scratch "turn:<uuid>" id — no row to point at.
            conv_uuid = None

    from app.models.shared_build import new_share_token

    # Minted here rather than left to the column default: reading it back off
    # the row after commit would touch an expired attribute on an async session.
    token = new_share_token()
    try:
        async with AsyncSessionLocal() as db:
            db.add(
                SharedBuild(
                    token=token,
                    build=snapshot,
                    build_key=build_key,
                    conversation_id=conv_uuid,
                )
            )
            await db.commit()
            return token
    except Exception:
        logger.warning("could not create shared build snapshot", exc_info=True)
        return None


# --- Public API — orchestrate the full flow -----------------------------------


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
      {"type": "checkpoint", "checkpoint_id": "...", "data": {...}}
      {"type": "done"}

    "reference_estimate" fires at most once per conversation, the first time
    a reference build is resolved for it (whether that's the budget-still-
    unknown estimate or the one resolved alongside a completed turn) — the
    route consumes it internally to cache the build onto the conversation
    row; it is not meant to replace the "build" event on the client.

    The "usage" event totals every OpenRouter call made this turn; the route
    consumes it internally (it is not forwarded to the client) to increment the
    conversation's running cost.

    "checkpoint" carries the graph's final state for this turn, for the caller
    to mirror into Postgres alongside everything else it persists. Also internal;
    it arrives after "done" because it describes a turn that has finished.

    conversation_id (when the caller has one — guests won't) does double duty:
    it is the graph's thread_id, which is what makes the accumulated build
    profile survive from one turn to the next, and it is passed to OpenRouter as
    session_id so every call this turn makes groups into one session in their
    dashboard. Guests fall back to a fresh scratch id, which gives them
    per-turn grouping and no persistence — the same deal they had before.

    THIS FUNCTION IS A PASSTHROUGH BY DESIGN. Every event below is produced by a
    node writing to the graph's custom stream; none is synthesized here. Keeping
    it that way is what lets the graph be rearranged without the SSE contract —
    which three callers and the frontend depend on — moving underneath it.
    """
    from app.services.graph.graph import get_graph
    from app.services.graph.state import new_usage

    is_guest = conversation_id is None
    thread_id = conversation_id or f"turn:{uuid.uuid4()}"
    config: dict = {"configurable": {"thread_id": thread_id}}

    graph = await get_graph()

    # Group this turn's spans into a LangSmith Thread. Set before astream so the
    # attribute lands on the span every node's work descends from — LangSmith
    # reads thread metadata off the trace root, and setting it inside a node
    # would tag that node's subtree only.
    #
    # thread_id, not conversation_id: a guest turn has no conversation row, and
    # its synthetic "turn:<uuid>" still deserves to be a (single-turn) thread
    # rather than an untagged orphan.
    attach_thread(thread_id)
    attach_run_metadata(is_guest=is_guest)

    initial: dict = {
        "messages": [m.model_dump() for m in messages],
        "conversation_id": conversation_id,
        "session_id": thread_id,
        "usage": new_usage(),
        # Reset per turn; only `profile` and `asked_fields` are meant to carry,
        # and those are restored from the checkpoint rather than passed in.
        "next_question": None,
        "price_estimate": None,
        "build_key": None,
        "build_data": None,
        "ref_estimate_key": None,
        "ref_estimate_data": None,
        # A pause belongs to the turn that paused. Left set, a later turn that
        # reached the builder and finished would route to `finalize` on a stale
        # flag and never present the build it just made.
        "build_paused": False,
        "case_options": None,
    }

    async for _mode, event in graph.astream(initial, config, stream_mode=["custom"]):
        yield event

    if is_guest:
        # No conversation row to mirror onto, so there is nothing to emit and
        # nothing to read back later.
        return

    checkpoint_event = await _checkpoint_event(graph, config)
    if checkpoint_event is not None:
        yield checkpoint_event


async def resume_chat_turn(
    token: str,
    case_name: str,
    conversation_id: str | None = None,
) -> AsyncIterator[dict]:
    """Finish a build the user has just chosen a case for.

    Yields the same event vocabulary as run_chat_turn, so every consumer —
    the transport adapter, the turn runner's persistence, the Valkey stream —
    works on a resumed turn without knowing it is one.

    DELIBERATELY NOT A GRAPH RUN. The graph exists to decide what a turn should
    do from the conversation; that decision was already made and acted on by
    the turn that paused. Re-entering it would re-extract the profile and
    re-route, spending two LLM calls to arrive back at "build" — and risking a
    different answer than the pipeline that is already half-finished. So this
    drives the remaining pipeline steps directly. The accumulated graph state
    (`profile`, `asked_fields`) is untouched and stays valid for the next turn.

    A resume that cannot be claimed yields an apology and nothing else: the
    parts were never shown, but the case options were, and quietly building
    something different would replace a choice the user made with one they
    did not.
    """
    from app.services.graph.state import new_usage

    progress: asyncio.Queue = asyncio.Queue()

    def _progress(step: str, message: str) -> None:
        progress.put_nowait({"type": "progress", "step": step, "message": message})

    yield {"type": "progress", "step": "resuming", "message": "Finishing your build…"}

    resumed = await resume_build(
        token,
        case_name,
        conversation_id=conversation_id,
        progress_callback=_progress,
    )

    # Drain whatever the pipeline emitted while it ran. Collected rather than
    # interleaved because resume_build is awaited as a unit; the steps left
    # after the case are short enough that ordering them live buys nothing.
    while not progress.empty():
        yield progress.get_nowait()

    if resumed is None:
        yield {
            "type": "token",
            "text": (
                "That build session has expired, so I can't finish it from here. "
                "Tell me what you're building and I'll put together a fresh one."
            ),
        }
        yield {"type": "usage", **new_usage()}
        yield {"type": "done"}
        return

    # Locks the picker on the message that showed it — matched by token, so it
    # lands on the earlier turn rather than this one. See transport._apply_event.
    yield {"type": "case_options", "data": resumed.case_options}

    payload = _build_payload_from_dict(resumed.build, resumed.profile)
    share_token = await create_shared_build(payload, "custom_dspy", conversation_id)
    if share_token is not None:
        payload["share_token"] = share_token
    yield {"type": "build", "key": "custom_dspy", "data": payload}
    yield {
        "type": "progress",
        "step": "presenting",
        "message": "Preparing your recommendation…",
    }

    usage = new_usage()
    sink: dict[str, Any] = {}
    async for chunk in stream_recommendation(
        resumed.messages,
        resumed.profile,
        "custom_dspy",
        payload,  # type: ignore[arg-type]
        usage_sink=sink,
        session_id=resumed.session_id,
    ):
        yield {"type": "token", "text": chunk}

    await _finalize_usage(sink)
    _merge_usage(usage, sink)
    yield {"type": "usage", **usage}
    yield {"type": "done"}


def _build_payload_from_dict(build: dict, profile: BuildProfile) -> dict:
    """_build_payload for an already-assembled dict rather than a Build."""
    return {
        "label": build["label"],
        "description": build["description"],
        "total_approx": build["total_approx"],
        "parts": build["parts"],
        "profile": profile.model_dump(),
    }


async def _checkpoint_event(graph, config: dict) -> dict | None:
    """Render the turn's final checkpoint for the Postgres write-behind.

    Read back through the checkpointer rather than assembled from the state we
    just streamed, so what gets mirrored is exactly what the checkpointer would
    hand back on the next turn — including the parts of the checkpoint (channel
    versions, step counters) this module knows nothing about.

    Returns None when there is nothing to mirror, which is the normal case for
    an in-memory fallback run.
    """
    from app.services.graph.checkpoint import AsyncValkeySaver

    saver = getattr(graph, "checkpointer", None)
    if not isinstance(saver, AsyncValkeySaver):
        return None
    try:
        tuple_ = await saver.aget_tuple(config)
        if tuple_ is None:
            return None
        return {
            "type": "checkpoint",
            "checkpoint_id": tuple_.checkpoint["id"],
            "data": saver.to_mirror(tuple_),
        }
    except Exception:
        logger.warning(
            "could not read back the turn's checkpoint to mirror it", exc_info=True
        )
        return None
