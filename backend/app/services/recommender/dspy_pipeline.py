"""
dspy_pipeline.py
================
Central DSPy pipeline orchestrator.

Invoked once the frontend has collected use cases, budget, and Q&A answers.
Runs components in dependency order, passing each decision into the next
as a hard constraint.

Dependency order:
    DDR → CPU → Cooler → Motherboard → RAM → Storage → GPU → PSU → Case → Fans

Budget allocation:
    _allocate_budget() derives per-slot ceilings from the total budget.
    These are independent soft maximums passed to the DB query layer — they
    don't have to sum to the total budget (ram/storage in particular are
    sized generously since not every slot maxes out at once), and they
    don't prevent the LLM from picking a cheaper option (and it should,
    often).

Status messages:
    Each step emits one stable, user-facing progress message up front (before
    the DB query) via _emit(). DSPy's own per-callback sub-messages
    (module_start / lm_start) are intentionally swallowed (_noop_status) so the
    frontend keeps showing that one message for the whole step instead of
    flickering — e.g. "Building your PC…" stays put through the entire DDR step
    and only changes when the next step begins.
    The pipeline is fully async — await run_pipeline() from an async context.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import dspy
from dspy.streaming.messages import StatusMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.crud import components as crud_components
from app.models.build_session import BuildSessionStatus
from app.schemas.chat import NO_BUDGET_CEILING, BuildRequest
from app.services.recommender.catalog_match import (
    CatalogRequirements,
    resolve_requirements,
)
from app.services.recommender.components.decidecase import DecideCase
from app.services.recommender.components.decidecase import load_program as load_case
from app.services.recommender.components.decidecpu import DecideCPU
from app.services.recommender.components.decidecpu import load_program as load_cpu
from app.services.recommender.components.decidecpucooler import DecideCPUCooler
from app.services.recommender.components.decidecpucooler import (
    load_program as load_cooler,
)
from app.services.recommender.components.decideddr import DecideDDR
from app.services.recommender.components.decideddr import load_program as load_ddr
from app.services.recommender.components.decidefans import DecideFans
from app.services.recommender.components.decidefans import load_program as load_fans
from app.services.recommender.components.decidegpu import DecideGPU
from app.services.recommender.components.decidegpu import load_program as load_gpu
from app.services.recommender.components.decidemotherboard import DecideMotherboard
from app.services.recommender.components.decidemotherboard import (
    load_program as load_motherboard,
)
from app.services.recommender.components.decidepsu import DecidePSU
from app.services.recommender.components.decidepsu import load_program as load_psu
from app.services.recommender.components.decideram import DecideRAM
from app.services.recommender.components.decideram import load_program as load_ram
from app.services.recommender.components.decidestorage import DecideStorage
from app.services.recommender.components.decidestorage import (
    load_program as load_storage,
)
from app.services.recommender.db.queries import (
    get_case_candidates,
    get_cooler_candidates,
    get_cpu_candidates,
    get_ddr_candidates,
    get_fan_candidates,
    get_gpu_chipset_candidates,
    get_motherboard_candidates,
    get_psu_candidates,
    get_ram_candidates,
    get_storage_candidates,
)
from app.services.recommender.recording import BuildRecorder
from app.services.recommender.scoring import find_dominant
from app.services.recommender.status_provider import BuildStatusProvider

logger = logging.getLogger(__name__)

# Module-level singleton — stateless, safe to share across concurrent requests
_status_provider = BuildStatusProvider()

# Model the Decide* modules run on. Routed through OpenRouter so every call
# returns cost/tokens uniformly (and model_name is meaningful for Haiku-vs-Gemma
# comparisons). Override RECOMMEND_MODEL to swap the model without a code
# change; an environment that also sets LLM_BASE_URL must, since these slugs are
# OpenRouter's names and no other server answers to them.
RECOMMEND_MODEL = os.getenv("RECOMMEND_MODEL", "openrouter/google/gemma-4-31b-it")

# Output token budget for every DSPy call — extraction and all ten Decide*
# steps. 1024 is ample for a model that answers directly: the largest output is
# ProfileExtraction's seventeen short fields.
#
# It is NOT ample for a reasoning model, whose thinking is charged to this same
# budget. Measured against qwen3.8-27b in LM Studio, extraction alone spent ~990
# tokens thinking and was cut off at the cap with EMPTY content — DSPy then
# parses no fields, every one defaults to 'unknown', and the turn never reaches
# the builder because is_profile_complete() keeps rejecting the profile. 4096
# was enough for the same call to finish. Raise this alongside the CHAT_*
# budgets in app/services/chat_models.py, never on its own.
RECOMMEND_MAX_TOKENS = int(os.getenv("RECOMMEND_MAX_TOKENS", "1024"))

# Dependency order of the Decide* steps — recorded as sequence_order so later
# decisions (which depend on earlier ones) can be reconstructed.
_SEQUENCE_ORDER: dict[str, int] = {
    "ddr": 0,
    "cpu": 1,
    "cooler": 2,
    "motherboard": 3,
    "ram": 4,
    "storage": 5,
    "gpu": 6,
    "psu": 7,
    "case": 8,
    "fans": 9,
}


# Use cases where a discrete GPU is a foregone conclusion, so the GPU step's
# `gpu_required` output carries no real decision. Only these are eligible for
# the dominance gate — see _step_gpu.
_DISCRETE_GPU_USE_CASES = frozenset(
    {"gaming", "streaming", "creator", "rendering", "aiml", "server"}
)


class NoValidCandidatesError(RuntimeError):
    """
    Raised when a Decide* step has zero eligible parts to choose from (e.g. no
    cooler in the DB fits the chosen CPU's socket/TDP within budget).

    Handled the same way as any other pipeline failure: run_pipeline/
    run_pipeline_post_case catch it, record status=error, and the chat
    pipeline falls back to the reference build immediately.
    """

    def __init__(self, step: str) -> None:
        super().__init__(f"No valid candidates for step '{step}'")
        self.step = step


def _ensure_candidates(step: str, candidates_json: str) -> None:
    """Fail fast if a step's candidate query came back empty.

    An empty candidate list means the LLM would be asked to choose from
    nothing — there's no valid part in the DB compatible with the decisions
    made so far, so continuing would only produce a hallucinated pick.
    """
    try:
        candidates = json.loads(candidates_json)
    except (TypeError, ValueError):
        candidates = None
    if not candidates:
        raise NoValidCandidatesError(step)


def _resolve_pipeline_version() -> str:
    """GEPA cohort key: pinned PIPELINE_VERSION env, else the git short SHA."""
    env = os.getenv("PIPELINE_VERSION")
    if env:
        return env
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=os.path.dirname(__file__),
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
            or "unknown"
        )
    except Exception:
        return "unknown"


PIPELINE_VERSION = _resolve_pipeline_version()


# ---------------------------------------------------------------------------
# DSPy configuration
# ---------------------------------------------------------------------------

# Base extra_body sent on every OpenRouter call. Copied (not mutated) per
# session so the "usage" flag survives alongside a per-session session_id.
_OPENROUTER_EXTRA_BODY: dict[str, Any] = {"usage": {"include": True}}


def _openrouter_extra_body() -> dict[str, Any]:
    """The base extra_body, plus the provider pin when one is configured.

    A function rather than a module constant because OPENROUTER_PROVIDER is
    settings, and freezing it at import time would make the pin depend on
    whether this module was imported before or after the environment was read.
    """
    body = dict(_OPENROUTER_EXTRA_BODY)
    if settings.OPENROUTER_PROVIDER:
        body["provider"] = settings.OPENROUTER_PROVIDER
    return body


def litellm_model(name: str) -> str:
    """Normalise a model slug to the provider prefix litellm needs right now.

    litellm routes on the prefix, not on api_base: `openrouter/…` sends the
    request to OpenRouter no matter what api_base says, so pointing DSPy at a
    local server means re-prefixing as `openai/…` (litellm's name for "an
    OpenAI-compatible endpoint", which is what LM Studio serves).

    Rewriting rather than demanding the caller get it right keeps the existing
    RECOMMEND_MODEL / GEPA_REFLECTION_MODEL values and their defaults working
    unchanged — an environment on LM Studio only has to name a model it has
    loaded, not also learn litellm's prefix convention.
    """
    if settings.chat_endpoint.is_openrouter:
        return name
    bare = name.split("/", 1)[1] if name.startswith("openrouter/") else name
    return bare if bare.startswith("openai/") else f"openai/{bare}"


def configure_dspy() -> None:
    """Configure DSPy to run the Decide* modules. Call once at startup.

    Via OpenRouter, or — when LLM_BASE_URL is set — via whatever
    OpenAI-compatible server it names. The Decide* modules themselves do not
    change; only where their completions are served from.
    """
    endpoint = settings.chat_endpoint
    lm = dspy.LM(
        model=litellm_model(RECOMMEND_MODEL),
        api_key=endpoint.api_key,
        max_tokens=RECOMMEND_MAX_TOKENS,
        temperature=0.3,
        # api_base is ignored by litellm for an `openrouter/` model, so passing
        # None here on the OpenRouter path is the same as not passing it.
        api_base=None if endpoint.is_openrouter else endpoint.url,
        # Ask OpenRouter to include usage/cost accounting in the response so
        # litellm surfaces the real cost per call in the LM history. OpenRouter
        # extension, so omitted when talking to anything else.
        extra_body=_openrouter_extra_body() if endpoint.is_openrouter else None,
    )
    # track_usage lets prediction.get_lm_usage() return per-prediction tokens.
    dspy.configure(lm=lm, track_usage=True)


def load_all_programs() -> None:
    """Build every Decide* module once, so the weights files are read off the
    request path rather than on the first build.

    Each load_program is lru_cached, so this is what fills those caches; the
    step calls in run_pipeline then hit them. The caches are process-local and
    never invalidated — after re-running optimize() and writing new weights,
    a serving process must be restarted to pick them up.
    """
    for load in (
        load_ddr,
        load_cpu,
        load_cooler,
        load_motherboard,
        load_ram,
        load_storage,
        load_gpu,
        load_psu,
        load_case,
        load_fans,
    ):
        load()


def session_lm(session_id: str | None) -> dspy.LM:
    """
    Clone the globally-configured LM with OpenRouter's `session_id` set, so
    every call made under it groups into one session in OpenRouter's
    dashboard. With no session_id, returns the LM unchanged (no-op override).

    Use via `with dspy.context(lm=session_lm(...)):` around a run — dspy.context
    is safe to call from any thread/task, unlike dspy.configure (which is
    restricted to the one thread that first called it).

    Under load test this returns a local stub instead, so none of the eleven
    Decide* modules calls OpenRouter. Resolving it here rather than deeper is
    deliberate: every caller wraps the result in dspy.context, and DSPy carries
    its own settings into the worker threads it spawns — so the stub survives
    into places a ContextVar would not reach.
    """
    from app.core.loadtest import is_load_test

    if is_load_test():
        from app.core.loadtest_stubs import make_stub_lm

        return make_stub_lm()

    # `session_id` is an OpenRouter dashboard concept and travels in extra_body,
    # which litellm sends verbatim — so off OpenRouter there is nothing to group
    # calls into and nowhere safe to put the field.
    if not session_id or not settings.chat_endpoint.is_openrouter:
        return dspy.settings.lm
    return dspy.settings.lm.copy(
        extra_body={**_openrouter_extra_body(), "session_id": session_id}
    )


# ---------------------------------------------------------------------------
# Streamified execution helper
# ---------------------------------------------------------------------------


async def _call_streamified(
    program: dspy.Module,
    status_fn: Callable[[str], None],
    **kwargs: Any,
) -> dspy.Prediction:
    """
    Run a DSPy module wrapped with streamify and forward any StatusMessage
    objects to status_fn before returning the final Prediction.

    A fresh stream is created per call, so concurrent pipeline runs are
    fully isolated even though they share the same _status_provider singleton.
    """
    streamed = dspy.streamify(program, status_message_provider=_status_provider)
    result: dspy.Prediction | None = None
    async for item in streamed(**kwargs):
        if isinstance(item, StatusMessage) and item.message:
            status_fn(item.message)
        elif isinstance(item, dspy.Prediction):
            result = item
    if result is None:
        raise RuntimeError("DSPy streamify yielded no Prediction")
    return result


def _try_dominance(
    recorder: BuildRecorder | None,
    program: dspy.Module,
    candidates: str,
    candidate_name_key: str,
    output_field: str,
    extra_outputs: dict[str, Any] | None = None,
    **inputs: Any,
) -> dspy.Prediction | None:
    """Resolve a step without an LLM call when one candidate strictly dominates.

    Two distinct key names, because they genuinely differ. `candidate_name_key`
    is the field the *candidate row* carries — "name" for CPUs, "chipset" for
    GPU chipsets, set by the serializers in db/queries.py. `output_field` is
    what the *Decide\\* signature* emits — "cpu_name", "gpu_chipset" — and is
    what the caller reads off the returned Prediction. Collapsing the two would
    make the gate silently never fire, since `row.get("cpu_name")` is None on
    every candidate.

    Returns a Prediction shaped exactly like the one the Decide* module would
    have produced (so the caller's field access is unchanged), or None to fall
    through to the normal LLM path. `extra_outputs` supplies the fields the gate
    cannot itself decide — see the GPU call site.

    Never raises: a bug in scoring must degrade to "ask the model", which is the
    behaviour that existed before the gate.
    """
    start = time.perf_counter()
    try:
        rows = json.loads(candidates)
        if not isinstance(rows, list):
            return None
        dominant = find_dominant(rows, candidate_name_key)
        if dominant is None:
            return None

        outputs: dict[str, Any] = {
            output_field: dominant.name,
            "reason": dominant.reason,
            "reconsideration_threshold": dominant.reconsideration_threshold,
            **(extra_outputs or {}),
        }
        category = getattr(program, "category", "unknown")
        logger.info(
            "dominance gate resolved %s step without an LLM call: %s "
            "(%.1f%% ahead of runner-up, cheapest in a %d-candidate set)",
            category,
            dominant.name,
            dominant.margin * 100,
            len(rows),
        )
        if recorder is not None:
            recorder.record_deterministic_decision(
                category=category,
                sequence_order=_SEQUENCE_ORDER.get(category, -1),
                signature_name=getattr(program, "signature_name", category),
                signature_version=getattr(program, "signature_version", 1),
                candidates_json=candidates,
                input_state=inputs,
                output_decision={
                    **outputs,
                    "perf_score": dominant.row.get("perf_score"),
                    "street_price_usd": dominant.row.get("street_price_usd"),
                    "dominance_margin": round(dominant.margin, 4),
                },
                chosen_name=dominant.name,
                latency_ms=int((time.perf_counter() - start) * 1000),
            )
        return dspy.Prediction(**outputs)
    except Exception:  # pragma: no cover - defensive
        logger.debug("dominance gate failed; falling back to LLM", exc_info=True)
        return None


async def _run_step(
    recorder: BuildRecorder | None,
    program: dspy.Module,
    status_fn: Callable[[str], None],
    *,
    candidates: str,
    **inputs: Any,
) -> dspy.Prediction:
    """
    Run one Decide* module via _call_streamified and, if a recorder is present,
    capture the decision (candidates, inputs, usage, latency) into it.

    Recording is best-effort and reads program-level telemetry metadata
    (category / signature_name / signature_version / output_name_field) added to
    each Decide* class. With no recorder this is a plain _call_streamified call.
    """
    lm = dspy.settings.lm
    start = time.perf_counter()
    result = await _call_streamified(
        program, status_fn, candidates=candidates, **inputs
    )
    latency_ms = int((time.perf_counter() - start) * 1000)

    if recorder is not None:
        try:
            history_entry = (
                dict(lm.history[-1]) if getattr(lm, "history", None) else None
            )
        except Exception:
            history_entry = None
        category = getattr(program, "category", "unknown")
        name_field = getattr(program, "output_name_field", "")
        recorder.record_decision(
            category=category,
            sequence_order=_SEQUENCE_ORDER.get(category, -1),
            signature_name=getattr(program, "signature_name", category),
            signature_version=getattr(program, "signature_version", 1),
            candidates_json=candidates,
            input_state=inputs,
            prediction=result,
            history_entry=history_entry,
            chosen_name=getattr(result, name_field, None) if name_field else None,
            latency_ms=latency_ms,
        )
    return result


# ---------------------------------------------------------------------------
# Budget allocation
# ---------------------------------------------------------------------------

# Per-slot budget ceilings by use case, as a fraction of the total budget.
# These are independent soft maximums, not a partition — they are not
# required to sum to 1.0 and, for slots like ram/storage where a generous
# ceiling matters more than strict proportionality, deliberately don't. The
# LLM can and should go lower within each slot when value calls for it.
_BUDGET_CEILINGS: dict[str, dict[str, float]] = {
    "gaming": {
        "cpu": 0.17,
        "cooler": 0.05,
        "mobo": 0.10,
        "ram": 0.18,
        "storage": 0.20,
        "gpu": 0.50,
        "psu": 0.07,
        "case": 0.10,
        "fans": 0.04,
    },
    "streaming": {
        # Game + encode simultaneously: more CPU headroom than pure gaming,
        # GPU still carries the render load (and NVENC).
        "cpu": 0.23,
        "cooler": 0.06,
        "mobo": 0.10,
        "ram": 0.20,
        "storage": 0.20,
        "gpu": 0.38,
        "psu": 0.07,
        "case": 0.10,
        "fans": 0.04,
    },
    "aiml": {
        "cpu": 0.21,
        "cooler": 0.06,
        "mobo": 0.10,
        "ram": 0.30,
        "storage": 0.28,
        "gpu": 0.80,
        "psu": 0.08,
        "case": 0.05,
        "fans": 0.03,
    },
    "server": {
        # The only profile where the *platform* is the expensive part rather
        # than the GPU. A Threadripper and its board are a third of the build
        # before anything is plugged into them, RAM is bought 8 sticks at a
        # time (and registered ECC costs more per GB than consumer kits), and
        # the PSU has to carry several GPUs plus a 350W+ CPU. Case and fans
        # get less than any other profile: a machine that runs headless in a
        # closet gains nothing from a nice one.
        "cpu": 0.32,
        "cooler": 0.06,
        "mobo": 0.18,
        "ram": 0.35,
        "storage": 0.30,
        "gpu": 0.75,
        "psu": 0.10,
        "case": 0.05,
        "fans": 0.03,
    },
    "creator": {
        "cpu": 0.23,
        "cooler": 0.07,
        "mobo": 0.10,
        "ram": 0.30,
        "storage": 0.32,
        "gpu": 0.28,
        "psu": 0.08,
        "case": 0.07,
        "fans": 0.04,
    },
    "rendering": {
        # 3D rendering: strong CPU and plenty of RAM, GPU sized for the
        # renderer (Cycles/OptiX etc.) rather than display output.
        "cpu": 0.25,
        "cooler": 0.07,
        "mobo": 0.10,
        "ram": 0.30,
        "storage": 0.22,
        "gpu": 0.35,
        "psu": 0.08,
        "case": 0.05,
        "fans": 0.03,
    },
    "dev": {
        # Compile-heavy: cores, RAM, and fast storage; GPU nearly irrelevant.
        "cpu": 0.29,
        "cooler": 0.07,
        "mobo": 0.12,
        "ram": 0.36,
        "storage": 0.32,
        "gpu": 0.13,
        "psu": 0.07,
        "case": 0.09,
        "fans": 0.03,
    },
    "audio": {
        # Music production: single-core speed, RAM for sample libraries,
        # quiet build; discrete GPU barely matters.
        "cpu": 0.32,
        "cooler": 0.08,
        "mobo": 0.13,
        "ram": 0.34,
        "storage": 0.26,
        "gpu": 0.06,
        "psu": 0.08,
        "case": 0.10,
        "fans": 0.04,
    },
    "default": {
        "cpu": 0.20,
        "cooler": 0.05,
        "mobo": 0.10,
        "ram": 0.20,
        "storage": 0.22,
        "gpu": 0.42,
        "psu": 0.08,
        "case": 0.10,
        "fans": 0.04,
    },
}


def _allocate_budget(budget_usd: int, use_cases: list[str]) -> dict[str, int]:
    """Return per-slot budget ceilings in USD.

    Under the 'custom' tier (budget_usd is NO_BUDGET_CEILING) every slot gets
    the sentinel rather than a share of a total, because there is no total to
    take a share of. Splitting a notional huge number across the slots would
    reintroduce exactly the constraint the user declined to set — the point of
    'custom' is that no slot has a maximum, not that every slot has a large
    one. The CRUD layer drops its price filter on the same sentinel.
    """
    if budget_usd == NO_BUDGET_CEILING:
        return dict.fromkeys(_BUDGET_CEILINGS["default"], NO_BUDGET_CEILING)
    # Use the first recognized use case to pick a ceiling profile
    profile_key = next(
        (uc for uc in use_cases if uc in _BUDGET_CEILINGS),
        "default",
    )
    ceilings = _BUDGET_CEILINGS[profile_key]
    return {slot: int(budget_usd * pct) for slot, pct in ceilings.items()}


def _request_summary(request: BuildRequest) -> str:
    """
    Flatten the request into the `use_cases` input string the Decide*
    signatures expect: use cases plus preferences and Q&A answers.
    """
    parts = [f"Use cases: {', '.join(request.use_cases)}"]
    if request.budget_usd == NO_BUDGET_CEILING:
        # Stated in the summary as well as in each step's numeric budget input,
        # because the sentinel reads as nonsense on its own. This string is the
        # one piece of context every Decide* module receives, so it is the
        # cheapest place to say it once and have all ten see it.
        parts.append(
            "Budget: no ceiling — the user has explicitly said cost is not a "
            "constraint. Still choose parts that earn their price for this "
            "workload; unlimited budget is not licence to pick the most "
            "expensive part in every slot."
        )
    prefs = request.preferences
    pref_bits = []
    if prefs.form_factor != "no_preference":
        pref_bits.append(f"{prefs.form_factor} form factor")
    if prefs.color_theme:
        pref_bits.append(f"color theme: {prefs.color_theme}")
    if prefs.rgb_lighting:
        pref_bits.append("RGB lighting")
    if pref_bits:
        parts.append(f"Preferences: {', '.join(pref_bits)}")
    if request.answers:
        answers = "; ".join(
            f"{k}: {', '.join(v) if isinstance(v, list) else v}"
            for k, v in request.answers.items()
            if v
        )
        if answers:
            parts.append(f"Q&A: {answers}")
    return " | ".join(parts)


def _catalog_terms(request: BuildRequest) -> list[str]:
    """The free-text titles worth resolving against the catalogs.

    chat_pipeline puts the user's named games under "gaming.games" and their
    named apps/models under "general.workloads"; rendering.software carries a
    single name from the profile's rendering_software field. Anything else in
    `answers` is an enum the extraction step chose, not something the user
    typed, so there is nothing to match it against.
    """
    terms: list[str] = []
    for key in ("gaming.games", "general.workloads", "rendering.software"):
        value = request.answers.get(key)
        if isinstance(value, list):
            terms.extend(str(v) for v in value)
        elif value:
            terms.append(str(value))
    return terms


async def _resolve_catalog_requirements(
    session: AsyncSession, request: BuildRequest
) -> CatalogRequirements | None:
    """Look up catalog requirements, swallowing any failure.

    Returns None when there was nothing to look up or the lookup failed. This
    is enrichment layered on top of a pipeline that worked without it, so it
    gets no ability to fail a build.
    """
    terms = _catalog_terms(request)
    if not terms:
        return None
    try:
        answers = request.answers
        return await resolve_requirements(
            session,
            terms,
            ai_workload=(
                answers.get("ai.workload")
                if isinstance(answers.get("ai.workload"), str)
                else None
            ),
            workload_intensity=(
                answers.get("general.workload_intensity")
                if isinstance(answers.get("general.workload_intensity"), str)
                else None
            ),
        )
    except Exception:  # pragma: no cover - defensive
        logger.warning("catalog requirement lookup failed", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Pipeline state
# ---------------------------------------------------------------------------


@dataclass
class DSPyBuildState:
    """Accumulates decisions as the pipeline runs."""

    request: BuildRequest
    progress_callback: Callable[[str, str], None] | None = None

    # OpenRouter session id grouping every LLM call this build makes (ddr
    # through fans) under one session in OpenRouter's dashboard. Set once by
    # run_pipeline and reused by run_pipeline_post_case for the fans step.
    session_id: str = ""

    # Flattened use cases + preferences + Q&A, passed as the `use_cases`
    # input to every Decide* module. Set once by run_pipeline.
    use_case_summary: str = ""

    # Requirements resolved from the games / software / ai_models catalogs for
    # the titles the user named. Already folded into use_case_summary; kept on
    # state so later steps can read the structured floors (min_vram_gb and
    # friends) rather than re-parsing prose. None when nothing was named or the
    # lookup failed.
    catalog_requirements: CatalogRequirements | None = None

    # Decisions (populated step by step)
    cpu_name: str = ""
    cpu_socket: str = ""
    cpu_tdp_w: int = 65
    # cpu_ddr_gen is the single "platform" generation (DDR step's pick, kept when
    # the chosen CPU supports it); cpu_ddr_gens is the CPU's full supported set,
    # used to admit any compatible motherboard generation.
    cpu_ddr_gen: str = "ddr5"
    cpu_ddr_gens: list[str] = field(default_factory=lambda: ["ddr5"])
    # Advisory input to the GPU step — 0 means the catalog doesn't record it,
    # which is the norm for consumer parts.
    cpu_pcie_lanes: int = 0

    cooler_name: str = ""

    mobo_name: str = ""
    mobo_form_factor: str = "atx"
    # DDR generation of the *chosen* board — RAM must match this specific board,
    # not the CPU's whole supported set.
    mobo_ddr_gen: str = ""
    mobo_m2_slots: int = 2
    mobo_sata_ports: int = 4
    # Hard ceiling on gpu_count. Defaults to 1 so a board lookup that missed
    # produces a single-GPU build rather than an unbuildable multi-GPU one.
    mobo_pcie_x16_slots: int = 1
    # DIMM types the chosen board accepts. Empty = unconstrained, which is what
    # every consumer board records; a populated list is what keeps the RAM step
    # from offering a workstation board unbuffered kits it can't POST on.
    mobo_module_types: list[str] = field(default_factory=list)

    # For RAM/Storage/PSU/GPU the DSPy step picks a *group*; a deterministic step
    # then resolves the cheapest exact SKU (*_name, used for build assembly).
    ram_group: str = ""
    ram_name: str = ""

    # Storage is the one multi-part role whose members differ from each other
    # (a fast OS drive alongside bulk capacity), so it carries lists rather
    # than a name plus a count. Ordered primary-drive-first.
    storage_groups: list[str] = field(default_factory=list)
    storage_names: list[str] = field(default_factory=list)

    # gpu_chipset is chosen by the main DSPy step; gpu_name is the exact board,
    # resolved deterministically post-case once length + PSU constraints are known.
    #
    # gpu_count is a quantity of that one board, not a list: multi-GPU only pays
    # off when the cards are matched (tensor/pipeline parallelism needs them to
    # be), so a build never mixes chipsets here.
    gpu_chipset: str = ""
    gpu_name: str = ""
    gpu_count: int = 1
    # Per-card TDP. The PSU step multiplies by gpu_count; keeping it per-card
    # means the value stays comparable with the chipset row it came from.
    gpu_tdp_w: int = 0
    # The chipset vendor's own single-card PSU recommendation, per card. Unlike
    # TDP this already accounts for transient spikes — a 600W card that draws
    # 600W steady-state pulls far more for milliseconds at a time, which is what
    # actually trips a supply's OCP. 0 when the catalog doesn't record it.
    gpu_recommended_psu_w: int = 0
    gpu_required: bool = True

    psu_group: str = ""
    psu_name: str = ""
    psu_form_factor: str = "atx"

    case_options: list[dict] = field(default_factory=list)
    case_name: str = ""  # set after user picks
    case_max_gpu_length_mm: int | None = None
    case_included_fans: int = 0
    case_fan_slots: list[int] = field(default_factory=list)

    fans_name: str = ""
    fans_quantity: int = 1

    error: str | None = None

    # Reconsideration thresholds — surfaced to user in the build card
    thresholds: dict[str, str] = field(default_factory=dict)


# Stand-in for an unstated form factor, applied only where a *physical size* is
# required. UserPreferences.form_factor defaults to "no_preference", which is a
# real and useful value to the motherboard query — crud.get_motherboard_candidates
# reads it as "do not filter", so a user with no preference correctly sees ATX,
# mATX and ITX boards alike. It is not a size, though, and any consumer that needs
# to reason about clearance has to be handed one.
#
# ATX because it is the roomiest of the three: a part sized for it is the least
# constrained choice, which is the right bias while nothing downstream actually
# enforces clearance (see get_cooler_candidates in db/queries.py).
#
# Resolved here rather than in the schema on purpose. Defaulting
# UserPreferences.form_factor to "atx" would silently narrow every motherboard
# query to ATX-only for users who never expressed a preference.
_ASSUMED_FORM_FACTOR = "atx"


def _effective_form_factor(preference: str) -> str:
    """Concrete form factor for size-sensitive queries."""
    return _ASSUMED_FORM_FACTOR if preference == "no_preference" else preference


def _emit(state: DSPyBuildState, step: str, message: str) -> None:
    if state.progress_callback:
        state.progress_callback(step, message)


def _noop_status(_msg: str) -> None:
    """Swallow DSPy's per-callback status messages (module_start / lm_start)."""
    return None


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------


async def _step_ddr(
    state: DSPyBuildState,
    session: AsyncSession,
    budget: dict,
    program: DecideDDR,
    recorder: BuildRecorder | None,
) -> None:
    _emit(state, "ddr", "Building your PC…")
    candidates = await get_ddr_candidates(session, budget["cpu"])
    _ensure_candidates("ddr", candidates)
    result = await _run_step(
        recorder,
        program,
        status_fn=_noop_status,
        use_cases=state.use_case_summary or str(state.request.use_cases),
        budget_total=state.request.budget_usd,
        candidates=candidates,
    )
    state.cpu_ddr_gen = result.ddr_gen
    state.thresholds["ddr"] = result.reconsideration_threshold


async def _step_cpu(
    state: DSPyBuildState,
    session: AsyncSession,
    budget: dict,
    program: DecideCPU,
    recorder: BuildRecorder | None,
) -> None:
    _emit(state, "cpu", "Choosing your CPU…")
    candidates = await get_cpu_candidates(
        session,
        budget["cpu"],
        state.request.preferences,
        state.request.use_cases,
        state.request.answers,
    )
    _ensure_candidates("cpu", candidates)
    step_inputs: dict[str, Any] = {
        "use_cases": state.use_case_summary or str(state.request.use_cases),
        "budget_total": state.request.budget_usd,
        "cpu_budget_ceiling": budget["cpu"],
    }
    result = _try_dominance(
        recorder,
        program,
        candidates,
        candidate_name_key="name",
        output_field="cpu_name",
        **step_inputs,
    ) or await _run_step(
        recorder,
        program,
        status_fn=_noop_status,
        candidates=candidates,
        **step_inputs,
    )
    state.cpu_name = result.cpu_name
    state.thresholds["cpu"] = result.reconsideration_threshold
    cpu = await crud_components.get_cpu_by_name(session, result.cpu_name)
    if cpu is None:
        # The chosen name didn't resolve to a catalog CPU even after tolerant
        # matching. Proceeding would leave socket/tdp/ddr at their defaults and
        # silently produce an incompatible build, so fail the step and fall back
        # to the reference build instead.
        raise RuntimeError(f"Chosen CPU {result.cpu_name!r} not found in catalog")
    state.cpu_socket = cpu.socket
    state.cpu_tdp_w = cpu.tdp_watts
    state.cpu_pcie_lanes = cpu.pcie_lanes or 0
    gens = [g for g in (cpu.ddr_generation or []) if g and g.strip()]
    if gens:
        state.cpu_ddr_gens = gens
        # Keep the DDR step's platform pick when the CPU actually supports
        # it; otherwise fall back to the CPU's newest supported generation.
        supported = {g.strip().lower() for g in gens}
        if state.cpu_ddr_gen.strip().lower() not in supported:
            state.cpu_ddr_gen = gens[-1]


async def _step_cooler(
    state: DSPyBuildState,
    session: AsyncSession,
    budget: dict,
    program: DecideCPUCooler,
    recorder: BuildRecorder | None,
) -> None:
    _emit(state, "cooler", "Picking a cooler…")
    candidates = await get_cooler_candidates(
        session,
        state.cpu_tdp_w,
        state.cpu_socket,
        budget["cooler"],
        # Concrete size, never "no_preference" — the cooler path needs something
        # to size clearance against. The motherboard step below deliberately does
        # NOT do this; there "no_preference" means "do not filter".
        _effective_form_factor(state.request.preferences.form_factor),
    )
    _ensure_candidates("cooler", candidates)
    result = await _run_step(
        recorder,
        program,
        status_fn=_noop_status,
        use_cases=state.use_case_summary or str(state.request.use_cases),
        cpu_name=state.cpu_name,
        cpu_tdp_w=state.cpu_tdp_w,
        budget_ceiling=budget["cooler"],
        candidates=candidates,
    )
    state.cooler_name = result.cooler_name
    state.thresholds["cooler"] = result.reconsideration_threshold


async def _step_motherboard(
    state: DSPyBuildState,
    session: AsyncSession,
    budget: dict,
    program: DecideMotherboard,
    recorder: BuildRecorder | None,
) -> None:
    _emit(state, "motherboard", "Selecting a motherboard…")
    candidates = await get_motherboard_candidates(
        session,
        cpu_socket=state.cpu_socket,
        ddr_gens=state.cpu_ddr_gens,
        budget_ceiling_usd=budget["mobo"],
        form_factor=state.request.preferences.form_factor,
        wifi_required=state.request.preferences.wifi_required,
    )
    _ensure_candidates("motherboard", candidates)
    result = await _run_step(
        recorder,
        program,
        status_fn=_noop_status,
        use_cases=state.use_case_summary or str(state.request.use_cases),
        cpu_name=state.cpu_name,
        ddr_gen=", ".join(state.cpu_ddr_gens),
        budget_ceiling=budget["mobo"],
        candidates=candidates,
    )
    state.mobo_name = result.motherboard_name
    state.thresholds["motherboard"] = result.reconsideration_threshold
    mobo = await crud_components.get_motherboard_by_name(
        session, result.motherboard_name
    )
    if mobo:
        state.mobo_form_factor = mobo.form_factor
        state.mobo_ddr_gen = mobo.ddr_generation or ""
        state.mobo_m2_slots = mobo.m2_slots or 0
        state.mobo_sata_ports = mobo.sata_ports or 0
        state.mobo_module_types = list(mobo.memory_module_types or [])
        # Floor of 1: a board with no recorded slot count still takes one card,
        # and reading the NULL as zero would produce a GPU-less build.
        state.mobo_pcie_x16_slots = max(1, mobo.pcie_x16_slots or 1)


async def _step_ram(
    state: DSPyBuildState,
    session: AsyncSession,
    budget: dict,
    program: DecideRAM,
    recorder: BuildRecorder | None,
) -> None:
    _emit(state, "ram", "Choosing RAM…")
    # RAM must match the generation of the board that was actually chosen, not
    # the CPU's whole supported set; fall back to the platform pick if the board
    # lookup missed.
    ddr_for_ram = state.mobo_ddr_gen or state.cpu_ddr_gen
    candidates = await get_ram_candidates(
        session,
        ddr_for_ram,
        budget["ram"],
        # Registered vs unbuffered is a wall, not a preference — the chosen
        # board decides it, same as it decides the generation.
        state.mobo_module_types,
    )
    _ensure_candidates("ram", candidates)
    result = await _run_step(
        recorder,
        program,
        status_fn=_noop_status,
        use_cases=state.use_case_summary or str(state.request.use_cases),
        ddr_gen=ddr_for_ram,
        budget_ceiling=budget["ram"],
        candidates=candidates,
    )
    state.ram_group = result.ram_group
    state.thresholds["ram"] = result.reconsideration_threshold
    # Resolve the cheapest kit in the chosen group (deterministic; no LLM call).
    kit = _pick_exact(
        await crud_components.get_ram_kits_for_group(session, result.ram_group)
    )
    if kit is not None:
        state.ram_name = kit.name


# Backstops on how many parts a single step may return. The per-step ceilings
# passed to the LLM (max_gpu_slots, max_drives, empty_fan_slots) are the real
# constraint; these only bound the damage when a model ignores them, so they
# sit well above any plausible real build rather than trying to be accurate.
_MAX_STORAGE_DRIVES = 6
_MAX_GPUS = 8
_MAX_FANS = 12

# Everything drawing power that neither the CPU's nor the GPU's TDP covers:
# board, RAM, drives, fans, pump, USB devices. Sizing a supply from those two
# figures alone silently under-counts a real machine by this much before any
# headroom multiplier is applied.
_PLATFORM_OVERHEAD_W = 100


def _parse_name_list(raw: str, limit: int) -> list[str]:
    """Comma-separated model output -> a de-duplicated, capped list of names.

    DSPy output fields are strings, so a step that picks several parts returns
    them as one. Duplicates are dropped rather than counted: UNIQUE (build_id,
    role, part_id) rejects the same part twice in a role, and a model naming a
    drive twice means it repeated itself, not that it wants two.
    """
    seen: set[str] = set()
    names: list[str] = []
    for chunk in (raw or "").split(","):
        name = chunk.strip()
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        names.append(name)
        if len(names) == limit:
            break
    return names


def _clamp_count(raw, *, ceiling: int, label: str) -> int:
    """Coerce a model-supplied count into [1, ceiling].

    The ceilings here are physical — PCIe slots, drive mounts, fan mounts — so
    a model that overshoots would otherwise produce a build that cannot be
    assembled. Logged when it bites, because a step that regularly ignores its
    stated ceiling is a prompt problem worth seeing.
    """
    try:
        count = int(raw)
    except (TypeError, ValueError):
        logger.warning("%s: non-numeric count %r; using 1", label, raw)
        return 1
    clamped = max(1, min(count, ceiling))
    if clamped != count:
        logger.warning(
            "%s: count %d outside [1, %d]; clamped to %d",
            label,
            count,
            ceiling,
            clamped,
        )
    return clamped


def _pick_exact(exacts: list):
    """Pick the exact SKU for a chosen group. Street price now lives on the group
    (every member is priced identically), so there's nothing to optimize on —
    return the first active member deterministically. RAM/Storage/PSU use this;
    GPU has its own fit-aware resolver (length/power still vary per board)."""
    return exacts[0] if exacts else None


async def _step_storage(
    state: DSPyBuildState,
    session: AsyncSession,
    budget: dict,
    program: DecideStorage,
    recorder: BuildRecorder | None,
) -> None:
    _emit(state, "storage", "Selecting storage…")
    candidates = await get_storage_candidates(
        session,
        budget["storage"],
        state.mobo_m2_slots,
        state.mobo_sata_ports,
    )
    _ensure_candidates("storage", candidates)
    # Every mounting point the board actually has. Floored at 1 so a board with
    # neither figure recorded still gets a boot drive.
    max_drives = max(1, state.mobo_m2_slots + state.mobo_sata_ports)
    result = await _run_step(
        recorder,
        program,
        status_fn=_noop_status,
        use_cases=state.use_case_summary or str(state.request.use_cases),
        budget_ceiling=budget["storage"],
        max_drives=max_drives,
        candidates=candidates,
    )
    state.storage_groups = _parse_name_list(
        result.storage_groups, limit=min(max_drives, _MAX_STORAGE_DRIVES)
    )
    state.thresholds["storage"] = result.reconsideration_threshold

    # Resolve each chosen group to a concrete drive, dropping any the catalog
    # can't match. A group that resolves to nothing is silently skipped rather
    # than failing the build: the remaining drives are still a usable machine,
    # and the boot drive is the one that matters.
    names: list[str] = []
    for group in state.storage_groups:
        drive = _pick_exact(
            await crud_components.get_storage_drives_for_group(session, group)
        )
        if drive is not None:
            names.append(drive.name)
        else:
            logger.warning("storage group %r resolved to no drive; skipping", group)
    state.storage_names = names


async def _step_gpu(
    state: DSPyBuildState,
    session: AsyncSession,
    budget: dict,
    program: DecideGPU,
    recorder: BuildRecorder | None,
) -> None:
    _emit(state, "gpu", "Choosing GPU…")
    # The main step chooses a chipset; the exact board is resolved later, once
    # the case (length) and PSU (power) are known — see _resolve_gpu_variant.
    candidates = await get_gpu_chipset_candidates(
        session,
        budget["gpu"],
        state.request.preferences,
        state.request.use_cases,
        state.request.answers,
    )
    _ensure_candidates("gpu", candidates)
    # The board was chosen three steps ago and its x16 slot count is now the
    # hard ceiling on how many cards this build can host — there is no going
    # back to a wider board from here. That asymmetry is why the motherboard
    # step is told to favour multi-slot boards for GPU-heavy use cases.
    max_gpu_slots = min(state.mobo_pcie_x16_slots, _MAX_GPUS)
    step_inputs: dict[str, Any] = {
        "use_cases": state.use_case_summary or str(state.request.use_cases),
        "budget_total": state.request.budget_usd,
        "gpu_budget_ceiling": budget["gpu"],
        "max_gpu_slots": max_gpu_slots,
        "cpu_pcie_lanes": state.cpu_pcie_lanes,
    }
    # The gate ranks chipsets against each other; it cannot answer the GPU
    # step's other two questions. So it only runs when both are already settled:
    # a single-x16 board removes the gpu_count decision, and a use case in
    # _DISCRETE_GPU_USE_CASES removes the gpu_required one. For productivity,
    # dev, audio and NAS builds, "no discrete GPU at all" is a live and often
    # correct answer that no amount of benchmark leadership can rule out —
    # those always go to the model.
    dominance_eligible = max_gpu_slots == 1 and all(
        uc in _DISCRETE_GPU_USE_CASES for uc in state.request.use_cases
    )
    result = (
        _try_dominance(
            recorder,
            program,
            candidates,
            candidate_name_key="chipset",
            output_field="gpu_chipset",
            extra_outputs={"gpu_count": 1, "gpu_required": True},
            **step_inputs,
        )
        if dominance_eligible
        else None
    ) or await _run_step(
        recorder,
        program,
        status_fn=_noop_status,
        candidates=candidates,
        **step_inputs,
    )
    state.gpu_required = result.gpu_required
    if state.gpu_required:
        state.gpu_chipset = result.gpu_chipset
        state.gpu_count = _clamp_count(
            getattr(result, "gpu_count", 1), ceiling=max_gpu_slots, label="gpu"
        )
        state.thresholds["gpu"] = result.reconsideration_threshold
        # Size the PSU against the chipset's TDP (intrinsic to the chip, on the
        # GPUChipset group) so there's headroom regardless of which board resolves.
        variants = await crud_components.get_gpus_for_chipset(
            session, result.gpu_chipset
        )
        tdps = [
            v.chipset.tdp_watts for v in variants if v.chipset and v.chipset.tdp_watts
        ]
        if tdps:
            state.gpu_tdp_w = max(tdps)
        # Carried to _step_psu so the supply is sized against the vendor figure
        # rather than against TDP alone. This is the same column _resolve_gpu_variant
        # filters boards on — reading it here is what stops the PSU step from
        # choosing a unit that the variant resolver then has to reject.
        recs = [
            v.chipset.recommended_psu_watts
            for v in variants
            if v.chipset and v.chipset.recommended_psu_watts
        ]
        if recs:
            state.gpu_recommended_psu_w = max(recs)


async def _resolve_gpu_variant(state: DSPyBuildState, session: AsyncSession) -> None:
    """Deterministically pick the exact GPU board for the chosen chipset now that
    the case length and PSU are known: the first board that fits the case's max
    GPU length and the PSU's wattage. Not a DSPy decision. Price is uniform across
    a chipset's boards (it lives on the chipset), so there's nothing to optimize
    on beyond fit.

    If no board fits (rare — e.g. every variant is too long for the picked case),
    fall back to the first variant and warn, so the build still completes; the
    reference build remains the safety net for genuinely incompatible outcomes.
    """
    if not state.gpu_required or not state.gpu_chipset:
        return
    variants = await crud_components.get_gpus_for_chipset(session, state.gpu_chipset)
    if not variants:
        logger.warning("no GPU boards found for chosen chipset %r", state.gpu_chipset)
        return

    psu = (
        await crud_components.get_psu_by_name(session, state.psu_name)
        if state.psu_name
        else None
    )
    psu_watts = psu.group.wattage if (psu and psu.group) else None
    max_len = state.case_max_gpu_length_mm

    def _fits(v) -> bool:
        # length_mm is per-board (exact); recommended_psu_watts is chip-intrinsic (group).
        if max_len is not None and v.length_mm and v.length_mm > max_len:
            return False
        rec = v.chipset.recommended_psu_watts if v.chipset else None
        # Multiply the chip's single-card PSU recommendation by the card count:
        # the supply has to carry all of them, not the worst one.
        if psu_watts is not None and rec and psu_watts < rec * state.gpu_count:
            return False
        return True

    def _stacks(v) -> bool:
        """Whether gpu_count of this board can sit next to each other.

        A heuristic, not a guarantee. Boards are seated in alternating x16
        slots on nearly every consumer and workstation layout, so a card up to
        two slots wide clears its neighbour and a 3-slot card does not. The
        schema carries no per-case expansion-slot budget to check properly, so
        this narrows the field rather than proving fit — hence the fallback
        below rather than a hard failure.
        """
        if state.gpu_count <= 1:
            return True
        return not v.width_slots or v.width_slots <= 2

    # Prefer a board that both fits and stacks; fall back to merely fitting,
    # then to anything, so a build always completes.
    chosen = (
        next((v for v in variants if _fits(v) and _stacks(v)), None)
        or next((v for v in variants if _fits(v)), None)
        or variants[0]
    )
    if not _fits(chosen):
        logger.warning(
            "no %r board fits case length %smm / PSU %sW for %d card(s); using variant %r",
            state.gpu_chipset,
            max_len,
            psu_watts,
            state.gpu_count,
            chosen.name,
        )
    elif not _stacks(chosen):
        logger.warning(
            "no %r board narrow enough to fit %d alongside each other; using %r "
            "(%s slots wide)",
            state.gpu_chipset,
            state.gpu_count,
            chosen.name,
            chosen.width_slots,
        )
    state.gpu_name = chosen.name
    tdp = chosen.chipset.tdp_watts if chosen.chipset else None
    if tdp:
        state.gpu_tdp_w = tdp


async def _step_psu(
    state: DSPyBuildState,
    session: AsyncSession,
    budget: dict,
    program: DecidePSU,
    recorder: BuildRecorder | None,
) -> None:
    _emit(state, "psu", "Calculating power supply…")
    # Add 20% headroom over combined TDP. gpu_tdp_w is per card, so a four-card
    # build draws four times it — sizing against a single card here would put a
    # 750W supply on a 1600W machine.
    system_tdp = (
        state.cpu_tdp_w + state.gpu_tdp_w * state.gpu_count + _PLATFORM_OVERHEAD_W
    )
    min_wattage = int(system_tdp * 1.20)
    # TDP-plus-headroom is a floor, not an answer: it describes steady-state
    # draw, and a supply dies on transients. The vendor's own recommendation
    # already prices those in, so take whichever figure is larger.
    #
    # Multiplied by gpu_count deliberately, even though each card's figure
    # includes an allowance for the rest of the system and n cards therefore
    # over-count that allowance n-1 times. _resolve_gpu_variant rejects any
    # board whose chipset recommends more than `psu_watts / gpu_count`, so a
    # PSU sized any smaller than this is one the resolver will refuse to pair
    # with the very chipset that was chosen — and its fallback is to ignore the
    # mismatch and ship the build anyway. Over-provisioning a multi-GPU supply
    # is the cheaper error.
    if state.gpu_required and state.gpu_recommended_psu_w:
        min_wattage = max(min_wattage, state.gpu_recommended_psu_w * state.gpu_count)
    # Determine PSU form factor from case
    psu_form_factor = state.psu_form_factor  # updated after case step if ITX
    candidates = await get_psu_candidates(
        session, min_wattage, budget["psu"], psu_form_factor
    )
    _ensure_candidates("psu", candidates)
    result = await _run_step(
        recorder,
        program,
        status_fn=_noop_status,
        required_wattage=min_wattage,
        budget_ceiling=budget["psu"],
        candidates=candidates,
    )
    state.psu_group = result.psu_group
    unit = _pick_exact(
        await crud_components.get_psus_for_group(session, result.psu_group)
    )
    if unit is not None:
        state.psu_name = unit.name


async def _step_case(
    state: DSPyBuildState,
    session: AsyncSession,
    budget: dict,
    program: DecideCase,
    recorder: BuildRecorder | None,
) -> None:
    _emit(state, "case", "Picking case options for you…")
    candidates = await get_case_candidates(
        session,
        budget["case"],
        state.mobo_form_factor,
        state.psu_form_factor,
    )
    _ensure_candidates("case", candidates)
    result = await _run_step(
        recorder,
        program,
        status_fn=_noop_status,
        use_cases=state.use_case_summary or str(state.request.use_cases),
        mobo_form_factor=state.mobo_form_factor,
        budget_ceiling=budget["case"],
        candidates=candidates,
    )
    state.case_options = [
        {"name": result.option_1, "reason": result.option_1_reason},
        {"name": result.option_2, "reason": result.option_2_reason},
        {"name": result.option_3, "reason": result.option_3_reason},
    ]
    # Pipeline pauses here — case_name is set externally after user picks


async def _step_fans(
    state: DSPyBuildState,
    session: AsyncSession,
    budget: dict,
    program: DecideFans,
    recorder: BuildRecorder | None,
) -> None:
    _emit(state, "fans", "Checking airflow…")
    candidates = await get_fan_candidates(session, budget["fans"], state.case_fan_slots)
    empty_slots = max(1, len(state.case_fan_slots))
    result = await _run_step(
        recorder,
        program,
        status_fn=_noop_status,
        cpu_tdp_w=state.cpu_tdp_w,
        # Total heat, not per-card: four GPUs is four times the load the case
        # has to clear, and that is the whole reason to add fans.
        gpu_tdp_w=state.gpu_tdp_w * state.gpu_count,
        case_included_fans=state.case_included_fans,
        empty_fan_slots=empty_slots,
        budget_ceiling=budget["fans"],
        candidates=candidates,
    )
    if result.fan_name.upper() != "NONE":
        state.fans_name = result.fan_name
        state.fans_quantity = _clamp_count(
            getattr(result, "fan_quantity", 1),
            ceiling=min(empty_slots, _MAX_FANS),
            label="fans",
        )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run_pipeline(
    request: BuildRequest,
    session: AsyncSession,
    progress_callback: Callable[[str, str], None] | None = None,
    recorder: BuildRecorder | None = None,
) -> DSPyBuildState:
    """
    Run the full DSPy component-by-component pipeline.

    Each step emits progress via progress_callback at two levels:
      - A step-start message before the DB query (_emit).
      - DSPy-native messages (module_start, lm_start) from BuildStatusProvider,
        forwarded via _call_streamified as the module runs.

    Pass a BuildRecorder to capture per-decision telemetry (see recording.py).
    The pipeline pauses at the case step, so on the success path the recorder is
    NOT flushed here — reuse the same recorder for run_pipeline_post_case, which
    finalizes it. On error the recorder is flushed with status=error.

    Returns a DSPyBuildState with all decisions populated up to (but not
    including) the case selection, which requires a round-trip to the user.
    Call run_pipeline_post_case() once the user has picked their case.
    """
    state = DSPyBuildState(request=request, progress_callback=progress_callback)
    state.use_case_summary = _request_summary(request)
    # Resolve the titles the user actually named against the games / software /
    # ai_models catalogs and fold their published requirements into the summary
    # every Decide* step reads. Best-effort by design: no embeddings backfilled
    # (or no API key) yields an empty result and the summary is unchanged.
    state.catalog_requirements = await _resolve_catalog_requirements(session, request)
    if state.catalog_requirements is not None:
        summary = state.catalog_requirements.summary()
        if summary:
            state.use_case_summary = f"{state.use_case_summary}\n{summary}"
    if recorder is not None:
        # Before the first step, so every decision this run records carries the
        # floors it was made under. Snapshotting them is what makes the
        # appropriateness metrics' sufficiency term reproducible months later,
        # after the catalogs and embeddings have moved on.
        recorder.set_catalog_requirements(
            state.catalog_requirements
            if state.catalog_requirements is not None
            and not state.catalog_requirements.is_empty
            else None
        )
    state.session_id = (
        str(recorder.session_id) if recorder is not None else str(uuid.uuid4())
    )
    budget = _allocate_budget(request.budget_usd, request.use_cases)

    try:
        with dspy.context(lm=session_lm(state.session_id)):
            await _step_ddr(state, session, budget, load_ddr(), recorder)
            await _step_cpu(state, session, budget, load_cpu(), recorder)
            await _step_cooler(state, session, budget, load_cooler(), recorder)
            await _step_motherboard(
                state, session, budget, load_motherboard(), recorder
            )
            await _step_ram(state, session, budget, load_ram(), recorder)
            await _step_storage(state, session, budget, load_storage(), recorder)
            await _step_gpu(state, session, budget, load_gpu(), recorder)
            await _step_psu(state, session, budget, load_psu(), recorder)
            await _step_case(state, session, budget, load_case(), recorder)
            # Pipeline pauses — return state with case_options populated
    except Exception as exc:
        state.error = str(exc)
        if recorder is not None:
            recorder.finish(BuildSessionStatus.ERROR)

    return state


async def run_pipeline_post_case(
    state: DSPyBuildState,
    session: AsyncSession,
    case_name: str,
    recorder: BuildRecorder | None = None,
) -> DSPyBuildState:
    """
    Resume the pipeline after the user has selected a case.
    Runs the fans step and finalizes the build.

    Pass the same BuildRecorder used for run_pipeline so decisions accumulate
    into one session; it is flushed here (status completed, or error on failure).
    """
    state.case_name = case_name
    if recorder is not None:
        recorder.record_case_choice(case_name)
    case = await crud_components.get_case_by_name(session, case_name)
    if case:
        state.case_included_fans = case.included_fan_count or 0
        state.case_max_gpu_length_mm = case.max_gpu_length_mm
        # Build fan slot list from available slots (e.g. three 120mm → [120, 120, 120])
        slot_size = 120  # default; cases don't store per-slot sizes separately
        total_slots = (case.max_fan_slots or 0) - state.case_included_fans
        state.case_fan_slots = [slot_size] * max(total_slots, 0)
    budget = _allocate_budget(state.request.budget_usd, state.request.use_cases)
    session_id = state.session_id or str(uuid.uuid4())
    try:
        # Case + PSU are both known now, so pin the exact GPU board for the
        # chipset the main step chose (deterministic; no LLM call).
        await _resolve_gpu_variant(state, session)
        with dspy.context(lm=session_lm(session_id)):
            await _step_fans(state, session, budget, load_fans(), recorder)
        if recorder is not None:
            recorder.finish(BuildSessionStatus.COMPLETED)
    except Exception as exc:
        state.error = str(exc)
        if recorder is not None:
            recorder.finish(BuildSessionStatus.ERROR)
    return state
