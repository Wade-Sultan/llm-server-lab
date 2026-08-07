"""The nodes of the chat turn graph.

Every node emits its user-visible output through `get_stream_writer()` rather
than returning it. Those writes come out of `graph.astream(stream_mode="custom")`
verbatim, which is what lets run_chat_turn stay a passthrough and the SSE event
contract stay untouched — a node's `writer({...})` is exactly the old `yield`.

The actual work still lives in app/services/chat_pipeline.py. These functions
decide sequencing and carry state; they do not reimplement extraction, the DSPy
pipeline, or either streaming call.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langgraph.config import get_stream_writer

from app.schemas.chat import BuildProfile
from app.services import chat_pipeline as cp
from app.services.chat_models import ChatModelConfig
from app.services.graph.state import ChatTurnState, merge_profile, new_usage

logger = logging.getLogger(__name__)


def _profile_of(state: ChatTurnState) -> BuildProfile:
    return BuildProfile(**(state.get("profile") or {}))


def _messages_of(state: ChatTurnState) -> list[Any]:
    from app.schemas.chat import ChatMessage

    return [ChatMessage(**m) for m in state.get("messages", [])]


# ---------------------------------------------------------------------------
# collect
# ---------------------------------------------------------------------------


async def collect(state: ChatTurnState) -> dict[str, Any]:
    """Extract this turn's profile and merge it over the checkpointed one.

    No progress event is emitted here, deliberately. The frontend treats any
    "progress" event as confirmation that the turn is on the recommend path, so
    nothing may fire until the router has decided — which is the next node.
    """
    usage = dict(state.get("usage") or new_usage())
    sink: dict[str, Any] = {}

    profile = await cp.extract_profile(
        _messages_of(state),
        usage_sink=sink,
        session_id=state.get("session_id"),
    )
    cp._merge_usage(usage, sink)

    merged = merge_profile(state.get("profile"), profile.model_dump())
    return {"profile": merged, "usage": usage}


# ---------------------------------------------------------------------------
# route
# ---------------------------------------------------------------------------

_ROUTER_SYSTEM = """\
You order intake questions for a PC build advisor. You are NOT talking to the user.

You will be given a numbered list of things still unknown about the user, and a
list of things already asked about in earlier turns. Reply with the NUMBER of the
single item that should be asked about next. Reply with the number only — no
words, no punctuation, no explanation.

Prefer, in order:
 1. An item that follows naturally from what the user just said.
 2. An item that has NOT already been asked about — if something was asked and
    the user talked around it, they are probably reluctant; come back to it later.
 3. Otherwise, the first item in the list.
"""


async def _pick_question(
    missing: list[str],
    asked: list[str],
    messages: list[Any],
    session_id: str | None,
    usage: dict[str, Any],
) -> str:
    """Choose which missing item to ask about next.

    Bounded by construction: the model returns an index into `missing`, so it
    cannot invent a field, cannot ask about something already known, and cannot
    decide the profile is complete. Anything unparseable or out of range falls
    back to missing[0], which is the hardcoded priority order this replaced.
    """
    if len(missing) == 1:
        return missing[0]

    numbered = "\n".join(f"{i + 1}. {item}" for i, item in enumerate(missing))
    prompt = f"Still unknown:\n{numbered}"
    if asked:
        prompt += "\n\nAlready asked about in earlier turns:\n" + "\n".join(
            f"- {item}" for item in asked
        )
    prompt += "\n\nReply with one number."

    api_messages: list[dict] = [{"role": "system", "content": _ROUTER_SYSTEM}]
    # Only the tail of the conversation: the router needs the user's last beat
    # to judge what follows naturally, not the whole history it would otherwise
    # pay for on every turn.
    for msg in messages[-4:]:
        api_messages.append(
            {
                "role": msg.role if msg.role in ("user", "assistant") else "user",
                "content": msg.content,
            }
        )
    api_messages.append({"role": "user", "content": prompt})

    sink: dict[str, Any] = {}
    try:
        client = cp._get_client()
        response = await client.chat.completions.create(
            model=ChatModelConfig.get_route_model(),
            max_tokens=8,
            temperature=0.0,
            messages=api_messages,
            **cp._extra_body(session_id),
        )
        sink.update(cp._usage_from_openai(getattr(response, "usage", None)))
        if getattr(response, "model", None):
            sink["model"] = response.model
        raw = (response.choices[0].message.content or "").strip()
    except Exception:
        logger.warning(
            "router question selection failed; using priority order", exc_info=True
        )
        return missing[0]
    finally:
        cp._merge_usage(usage, sink)

    digits = "".join(c for c in raw if c.isdigit())
    if not digits:
        logger.info("router returned %r, not an index; using priority order", raw)
        return missing[0]
    index = int(digits) - 1
    if not 0 <= index < len(missing):
        logger.info("router returned out-of-range index %s; using priority order", raw)
        return missing[0]
    return missing[index]


async def route(state: ChatTurnState) -> dict[str, Any]:
    """Decide whether to ask another question or hand off to the builder.

    THE SUFFICIENCY DECISION IS is_profile_complete()'S AND ONLY ITS. The model
    is never asked whether there is enough information — it is asked, and only
    when the answer is already known to be "no", which of the remaining gaps to
    raise next. That split is the whole point: a hallucinated readiness call
    ships a build against a profile nobody stated, while a badly ordered
    question costs one awkward exchange.
    """
    profile = _profile_of(state)
    usage = dict(state.get("usage") or new_usage())

    if cp.is_profile_complete(profile):
        return {"next_question": None, "missing_fields": [], "usage": usage}

    missing = cp._missing_fields(profile)
    if not missing:
        # is_profile_complete said no but _missing_fields found nothing, which
        # means the two have drifted apart. Building on a profile the gate
        # rejected is the worse of the two failures, so ask the catch-all.
        logger.error(
            "profile incomplete but no missing fields identified; "
            "is_profile_complete and _missing_fields have diverged"
        )
        missing = ["anything else about what they need the machine to do"]

    chosen = await _pick_question(
        missing,
        state.get("asked_fields") or [],
        _messages_of(state),
        state.get("session_id"),
        usage,
    )

    # Resolved here rather than in `ask` so the node that streams stays purely
    # about streaming. Only fires when budget is the sole remaining gap.
    price_estimate: int | None = None
    ref_key: str | None = None
    ref_data: dict[str, Any] | None = None
    if missing == ["budget expectations"]:
        est_key, est_build, est_cached = await cp._get_reference_build(
            profile, state.get("conversation_id"), assumed_budget_tier="mid"
        )
        price_estimate = cp._round_to_nearest_hundred_usd(est_build["total_approx"])
        if not est_cached:
            ref_key = est_key
            ref_data = cp._build_payload(est_build, profile)
            get_stream_writer()(
                {"type": "reference_estimate", "key": ref_key, "data": ref_data}
            )

    return {
        "next_question": chosen,
        "missing_fields": missing,
        "asked_fields": [chosen],
        "price_estimate": price_estimate,
        "ref_estimate_key": ref_key,
        "ref_estimate_data": ref_data,
        "usage": usage,
    }


def should_build(state: ChatTurnState) -> str:
    """Conditional edge out of `route`."""
    return "build" if state.get("next_question") is None else "ask"


# ---------------------------------------------------------------------------
# ask
# ---------------------------------------------------------------------------


async def ask(state: ChatTurnState) -> dict[str, Any]:
    """Stream one follow-up question about the item the router chose."""
    writer = get_stream_writer()
    usage = dict(state.get("usage") or new_usage())
    sink: dict[str, Any] = {}

    chosen = state.get("next_question")
    async for chunk in cp.stream_elicitation(
        _messages_of(state),
        # A one-item list: stream_elicitation appends "Ask about the FIRST
        # missing item only", so handing it the router's choice alone is what
        # makes the choice take effect.
        missing_fields=[chosen] if chosen else None,
        price_estimate=state.get("price_estimate"),
        usage_sink=sink,
        session_id=state.get("session_id"),
    ):
        writer({"type": "token", "text": chunk})

    cp._merge_usage(usage, sink)
    return {"usage": usage}


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


async def build(state: ChatTurnState) -> dict[str, Any]:
    """Run the fixed build pipeline: DSPy and the reference build, in parallel.

    Unchanged in substance from the pre-graph implementation. The progress
    queue is kept rather than having the DSPy progress callback call the stream
    writer directly, because _run_dspy_build already writes to it and the
    sentinel it enqueues last is how this loop learns the pipeline finished —
    success or failure — without having to await the task to find out.

    The whole drain is wrapped in one deadline rather than a per-item timeout:
    the budget is on the build, not on the gap between two progress messages,
    and a pipeline that emitted steady progress for an hour should still be cut
    off. Cancelling dspy_task is what makes the deadline real; _run_dspy_build
    catches the CancelledError to flush its telemetry and re-raises.
    """
    writer = get_stream_writer()
    profile = _profile_of(state)
    conversation_id = state.get("conversation_id")

    writer({"type": "progress", "step": "resolving", "message": "Building your PC…"})

    progress_queue: asyncio.Queue = asyncio.Queue()
    ref_task = asyncio.create_task(cp._get_reference_build(profile, conversation_id))
    dspy_task = asyncio.create_task(
        cp._run_dspy_build(profile, progress_queue, ref_task)
    )

    dspy_build: dict | None = None
    try:
        async with asyncio.timeout(cp._DSPY_CHAT_TIMEOUT_S):
            while True:
                item = await progress_queue.get()
                if item is cp._PIPELINE_DONE:
                    break
                writer(item)
            dspy_build = await dspy_task  # never raises; None on failure
    except TimeoutError:
        dspy_task.cancel()
        try:
            await dspy_task
        except asyncio.CancelledError:
            pass
        logger.warning(
            "DSPy pipeline timed out after %.0fs; using reference build",
            cp._DSPY_CHAT_TIMEOUT_S,
        )

    ref_key: str | None = None
    ref_data: dict[str, Any] | None = None

    if dspy_build is not None:
        # Customer sees the DSPy build. The reference build was already awaited
        # and recorded onto the same session row inside _run_dspy_build; it is
        # not cancelled (it is the recorded comparison), just reaped.
        build_key, built = "custom_dspy", dspy_build
        try:
            rkey, rbuild, rcached = await ref_task
            if not rcached:
                ref_key, ref_data = rkey, cp._build_payload(rbuild, profile)
        except Exception:
            logger.debug(
                "reference build task errored (already recorded/ignored)", exc_info=True
            )
    else:
        build_key, built, rcached = await ref_task
        if not rcached:
            ref_key, ref_data = build_key, cp._build_payload(built, profile)

    if ref_key is not None:
        writer({"type": "reference_estimate", "key": ref_key, "data": ref_data})

    payload = cp._build_payload(built, profile)
    writer({"type": "build", "key": build_key, "data": payload})
    writer(
        {
            "type": "progress",
            "step": "presenting",
            "message": "Preparing your recommendation…",
        }
    )

    return {
        "build_key": build_key,
        "build_data": payload,
        "ref_estimate_key": ref_key,
        "ref_estimate_data": ref_data,
    }


# ---------------------------------------------------------------------------
# present
# ---------------------------------------------------------------------------


async def present(state: ChatTurnState) -> dict[str, Any]:
    """Stream the short lead-in that accompanies the BuildCard."""
    writer = get_stream_writer()
    usage = dict(state.get("usage") or new_usage())
    sink: dict[str, Any] = {}

    profile = _profile_of(state)
    payload = state.get("build_data") or {}
    async for chunk in cp.stream_recommendation(
        _messages_of(state),
        profile,
        state.get("build_key") or "",
        payload,  # type: ignore[arg-type]
        usage_sink=sink,
        session_id=state.get("session_id"),
    ):
        writer({"type": "token", "text": chunk})

    cp._merge_usage(usage, sink)
    return {"usage": usage}


# ---------------------------------------------------------------------------
# finalize
# ---------------------------------------------------------------------------


async def finalize(state: ChatTurnState) -> dict[str, Any]:
    """Terminal node for both branches: emit the turn's usage total, then done.

    Having one terminal node rather than emitting these from `ask` and `present`
    separately is what keeps run_chat_turn a passthrough — it never has to
    synthesize an event, so there is exactly one place events are produced.
    """
    writer = get_stream_writer()
    writer({"type": "usage", **(state.get("usage") or new_usage())})
    writer({"type": "done"})
    return {}
