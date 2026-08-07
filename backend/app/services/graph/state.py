"""State carried through one chat turn, and persisted across turns.

WHAT IS CHECKPOINTED AND WHY IT MATTERS. Before the graph, every turn re-derived
the whole profile from raw conversation text and had no idea what it had already
asked. Two fields here change that:

  profile       accumulated, not replaced. A field the extractor found on turn 2
                and loses on turn 5 (buried under ten intervening messages) stays
                known. See merge_profile below.
  asked_fields  every item the router has already asked about, appended across
                turns. Lets the router notice it has asked twice and been dodged
                twice, and choose differently rather than a third time.

Everything else is per-turn scratch that happens to live in the same dict.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class ChatTurnState(TypedDict, total=False):
    # -- inputs, set fresh each turn --------------------------------------
    messages: list[dict[str, str]]
    conversation_id: str | None
    # OpenRouter's session_id, so every call this turn groups into one session
    # in their dashboard. Falls back to a per-turn id for guests.
    session_id: str

    # -- accumulated across turns -----------------------------------------
    profile: dict[str, Any] | None
    asked_fields: Annotated[list[str], operator.add]

    # -- per-turn scratch --------------------------------------------------
    missing_fields: list[str]
    # The single item the router chose to ask about, or None when the profile
    # is complete and the turn is going to the builder instead. This is the
    # value the conditional edge branches on.
    next_question: str | None
    price_estimate: int | None
    usage: dict[str, Any]
    build_key: str | None
    build_data: dict[str, Any] | None
    ref_estimate_key: str | None
    ref_estimate_data: dict[str, Any] | None


def new_usage() -> dict[str, Any]:
    """A zeroed per-turn usage total, shaped for chat_pipeline._merge_usage."""
    return {
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_usd": 0.0,
        "llm_call_count": 0,
        "models": [],
    }


# Fields where the extractor's "I didn't find it" is indistinguishable from
# "the user retracted it". Retraction is rare and correctable by asking again;
# forgetting is common and costs the user a repeated question, so these are
# merged forward rather than overwritten with None.
_STICKY_FIELDS = (
    "gaming_resolution",
    "gaming_fps",
    "streaming_style",
    "ai_workload",
    "ai_model_scale",
    "server_workload",
    "server_gpu_count",
    "editing_resolution",
    "rendering_software",
    "workload_intensity",
    "price_sensitivity",
    "form_factor",
    "color_theme",
    "rgb_lighting",
    "noise_tolerance",
)


def merge_profile(
    previous: dict[str, Any] | None, current: dict[str, Any]
) -> dict[str, Any]:
    """Merge a freshly extracted profile over the checkpointed one.

    Only fills gaps: a value the extractor produced this turn always wins, and
    a previous value is reused only where this turn produced nothing. That
    ordering matters — the user really can change their mind about resolution,
    and the extractor really does see the correction, so the fresh answer has
    to be able to overwrite the old one.

    primary_use and budget_tier are handled separately because their "unknown"
    sentinel is a value, not an absence, and would otherwise overwrite a known
    answer with an admission of ignorance.
    """
    if not previous:
        return current

    merged = dict(current)

    for field in _STICKY_FIELDS:
        if merged.get(field) is None and previous.get(field) is not None:
            merged[field] = previous[field]

    for field in ("primary_use", "budget_tier"):
        if merged.get(field) in (None, "unknown") and previous.get(field) not in (
            None,
            "unknown",
        ):
            merged[field] = previous[field]

    # List and free-text fields accumulate rather than replace: the user naming
    # a second game does not mean they stopped caring about the first.
    for field in ("games", "workloads"):
        combined = list(previous.get(field) or [])
        for item in merged.get(field) or []:
            if item not in combined:
                combined.append(item)
        merged[field] = combined

    if not (merged.get("notes") or "").strip() and previous.get("notes"):
        merged["notes"] = previous["notes"]

    return merged
