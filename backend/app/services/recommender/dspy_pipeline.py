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
    _allocate_budget() splits the total into per-slot ceilings.  These are
    soft maximums passed to the DB query layer — they don't prevent the LLM
    from picking a cheaper option (and it should, often).

Status messages:
    Each step emits two levels of progress via the optional progress_callback:
      1. A step-start message (before the DB query) from _emit().
      2. DSPy-native messages (module_start, lm_start) forwarded from
         BuildStatusProvider through _call_streamified().
    The pipeline is fully async — await run_pipeline() from an async context.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import dspy
from dspy.streaming.messages import StatusMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.build_session import BuildSessionStatus
from app.services.recommender.recording import BuildRecorder
from app.services.recommender.status_provider import BuildStatusProvider

from app.services.recommender.components.decidecase import DecideCase, load_program as load_case
from app.services.recommender.components.decidecpu import DecideCPU, load_program as load_cpu
from app.services.recommender.components.decideddr import DecideDDR, load_program as load_ddr
from app.services.recommender.components.decidecpucooler import DecideCPUCooler, load_program as load_cooler
from app.services.recommender.components.decidefans import DecideFans, load_program as load_fans
from app.services.recommender.components.decidegpu import DecideGPU, load_program as load_gpu
from app.services.recommender.components.decidemotherboard import DecideMotherboard, load_program as load_motherboard
from app.services.recommender.components.decidepsu import DecidePSU, load_program as load_psu
from app.services.recommender.components.decideram import DecideRAM, load_program as load_ram
from app.services.recommender.components.decidestorage import DecideStorage, load_program as load_storage
from app.services.recommender.db.queries import (
    get_case_candidates,
    get_cooler_candidates,
    get_cpu_candidates,
    get_ddr_candidates,
    get_fan_candidates,
    get_gpu_candidates,
    get_motherboard_candidates,
    get_psu_candidates,
    get_ram_candidates,
    get_storage_candidates,
)
from app.crud import components as crud_components
from app.schemas.chat import BuildRequest

# Module-level singleton — stateless, safe to share across concurrent requests
_status_provider = BuildStatusProvider()

# Model the Decide* modules run on. Routed through OpenRouter so every call
# returns cost/tokens uniformly (and model_name is meaningful for Haiku-vs-Gemma
# comparisons). Override RECOMMENDER_MODEL to swap the model without a code change.
RECOMMENDER_MODEL = os.getenv("RECOMMENDER_MODEL", "openrouter/anthropic/claude-haiku-4.5")

# Dependency order of the Decide* steps — recorded as sequence_order so later
# decisions (which depend on earlier ones) can be reconstructed.
_SEQUENCE_ORDER: dict[str, int] = {
    "ddr": 0, "cpu": 1, "cooler": 2, "motherboard": 3, "ram": 4,
    "storage": 5, "gpu": 6, "psu": 7, "case": 8, "fans": 9,
}


def _resolve_pipeline_version() -> str:
    """GEPA cohort key: pinned PIPELINE_VERSION env, else the git short SHA."""
    env = os.getenv("PIPELINE_VERSION")
    if env:
        return env
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(__file__),
            stderr=subprocess.DEVNULL,
        ).decode().strip() or "unknown"
    except Exception:
        return "unknown"


PIPELINE_VERSION = _resolve_pipeline_version()


# ---------------------------------------------------------------------------
# DSPy configuration
# ---------------------------------------------------------------------------

def configure_dspy() -> None:
    """Configure DSPy to run the Decide* modules via OpenRouter. Call once at startup."""
    lm = dspy.LM(
        model=RECOMMENDER_MODEL,
        api_key=settings.OPENROUTER_API_KEY,
        max_tokens=1024,
        temperature=0.3,
        # Ask OpenRouter to include usage/cost accounting in the response so
        # litellm surfaces the real cost per call in the LM history.
        extra_body={"usage": {"include": True}},
    )
    # track_usage lets prediction.get_lm_usage() return per-prediction tokens.
    dspy.configure(lm=lm, track_usage=True)


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
    result = await _call_streamified(program, status_fn, candidates=candidates, **inputs)
    latency_ms = int((time.perf_counter() - start) * 1000)

    if recorder is not None:
        try:
            history_entry = dict(lm.history[-1]) if getattr(lm, "history", None) else None
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

# Rough percentage splits by use case.  These are starting points — the LLM
# can and should go lower within each slot when value calls for it.
_BUDGET_SPLITS: dict[str, dict[str, float]] = {
    "gaming": {
        "cpu": 0.15, "cooler": 0.05, "mobo": 0.10, "ram": 0.07,
        "storage": 0.07, "gpu": 0.35, "psu": 0.07, "case": 0.10, "fans": 0.04,
    },
    "aiml": {
        "cpu": 0.18, "cooler": 0.06, "mobo": 0.10, "ram": 0.12,
        "storage": 0.10, "gpu": 0.28, "psu": 0.08, "case": 0.05, "fans": 0.03,
    },
    "creator": {
        "cpu": 0.20, "cooler": 0.07, "mobo": 0.10, "ram": 0.12,
        "storage": 0.12, "gpu": 0.20, "psu": 0.08, "case": 0.07, "fans": 0.04,
    },
    "default": {
        "cpu": 0.17, "cooler": 0.05, "mobo": 0.10, "ram": 0.08,
        "storage": 0.08, "gpu": 0.30, "psu": 0.08, "case": 0.10, "fans": 0.04,
    },
}

def _allocate_budget(budget_usd: int, use_cases: list[str]) -> dict[str, int]:
    """Return per-slot budget ceilings in USD."""
    # Use the first recognized use case to pick a split profile
    profile_key = next(
        (uc for uc in use_cases if uc in _BUDGET_SPLITS),
        "default",
    )
    splits = _BUDGET_SPLITS[profile_key]
    return {slot: int(budget_usd * pct) for slot, pct in splits.items()}


# ---------------------------------------------------------------------------
# Pipeline state
# ---------------------------------------------------------------------------

@dataclass
class DSPyBuildState:
    """Accumulates decisions as the pipeline runs."""
    request: BuildRequest
    progress_callback: Callable[[str, str], None] | None = None

    # Decisions (populated step by step)
    cpu_name: str = ""
    cpu_socket: str = ""
    cpu_tdp_w: int = 65
    cpu_ddr_gen: str = "ddr5"

    cooler_name: str = ""

    mobo_name: str = ""
    mobo_form_factor: str = "atx"
    mobo_m2_slots: int = 2
    mobo_sata_ports: int = 4

    ram_name: str = ""

    storage_name: str = ""

    gpu_name: str = ""
    gpu_tdp_w: int = 0
    gpu_required: bool = True

    psu_name: str = ""
    psu_form_factor: str = "atx"

    case_options: list[dict] = field(default_factory=list)
    case_name: str = ""              # set after user picks
    case_max_gpu_length_mm: int | None = None
    case_included_fans: int = 0
    case_fan_slots: list[int] = field(default_factory=list)

    fans_name: str = ""

    error: str | None = None

    # Reconsideration thresholds — surfaced to user in the build card
    thresholds: dict[str, str] = field(default_factory=dict)


def _emit(state: DSPyBuildState, step: str, message: str) -> None:
    if state.progress_callback:
        state.progress_callback(step, message)


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------

async def _step_ddr(state: DSPyBuildState, session: AsyncSession, budget: dict, program: DecideDDR, recorder: BuildRecorder | None) -> None:
    _emit(state, "ddr", "Deciding memory generation…")
    candidates = await get_ddr_candidates(session, budget["cpu"])
    result = await _run_step(
        recorder,
        program,
        status_fn=lambda msg: _emit(state, "ddr", msg),
        use_cases=str(state.request.use_cases),
        budget_total=state.request.budget_usd,
        candidates=candidates,
    )
    state.cpu_ddr_gen = result.ddr_gen
    state.thresholds["ddr"] = result.reconsideration_threshold


async def _step_cpu(state: DSPyBuildState, session: AsyncSession, budget: dict, program: DecideCPU, recorder: BuildRecorder | None) -> None:
    _emit(state, "cpu", "Choosing your CPU…")
    candidates = await get_cpu_candidates(session, budget["cpu"], state.request.preferences)
    result = await _run_step(
        recorder,
        program,
        status_fn=lambda msg: _emit(state, "cpu", msg),
        use_cases=str(state.request.use_cases),
        budget_total=state.request.budget_usd,
        cpu_budget_ceiling=budget["cpu"],
        candidates=candidates,
    )
    state.cpu_name = result.cpu_name
    state.thresholds["cpu"] = result.reconsideration_threshold
    cpu = await crud_components.get_cpu_by_name(session, result.cpu_name)
    if cpu:
        state.cpu_socket = cpu.socket
        state.cpu_tdp_w = cpu.tdp_watts
        state.cpu_ddr_gen = (cpu.ddr_generation or ["ddr5"])[-1]


async def _step_cooler(state: DSPyBuildState, session: AsyncSession, budget: dict, program: DecideCPUCooler, recorder: BuildRecorder | None) -> None:
    _emit(state, "cooler", "Picking a cooler…")
    candidates = await get_cooler_candidates(
        session, state.cpu_tdp_w, state.cpu_socket, budget["cooler"],
        state.request.preferences.form_factor,
    )
    result = await _run_step(
        recorder,
        program,
        status_fn=lambda msg: _emit(state, "cooler", msg),
        use_cases=str(state.request.use_cases),
        cpu_name=state.cpu_name,
        cpu_tdp_w=state.cpu_tdp_w,
        budget_ceiling=budget["cooler"],
        candidates=candidates,
    )
    state.cooler_name = result.cooler_name
    state.thresholds["cooler"] = result.reconsideration_threshold


async def _step_motherboard(state: DSPyBuildState, session: AsyncSession, budget: dict, program: DecideMotherboard, recorder: BuildRecorder | None) -> None:
    _emit(state, "motherboard", "Selecting a motherboard…")
    candidates = await get_motherboard_candidates(
        session,
        cpu_socket=state.cpu_socket,
        ddr_gen=state.cpu_ddr_gen,
        budget_ceiling_usd=budget["mobo"],
        form_factor=state.request.preferences.form_factor,
        wifi_required=state.request.preferences.wifi_required,
    )
    result = await _run_step(
        recorder,
        program,
        status_fn=lambda msg: _emit(state, "motherboard", msg),
        use_cases=str(state.request.use_cases),
        cpu_name=state.cpu_name,
        ddr_gen=state.cpu_ddr_gen,
        budget_ceiling=budget["mobo"],
        candidates=candidates,
    )
    state.mobo_name = result.motherboard_name
    state.thresholds["motherboard"] = result.reconsideration_threshold
    mobo = await crud_components.get_motherboard_by_name(session, result.motherboard_name)
    if mobo:
        state.mobo_form_factor = mobo.form_factor
        state.mobo_m2_slots = mobo.m2_slots or 0
        state.mobo_sata_ports = mobo.sata_ports or 0


async def _step_ram(state: DSPyBuildState, session: AsyncSession, budget: dict, program: DecideRAM, recorder: BuildRecorder | None) -> None:
    _emit(state, "ram", "Choosing RAM…")
    candidates = await get_ram_candidates(session, state.cpu_ddr_gen, budget["ram"])
    result = await _run_step(
        recorder,
        program,
        status_fn=lambda msg: _emit(state, "ram", msg),
        use_cases=str(state.request.use_cases),
        ddr_gen=state.cpu_ddr_gen,
        budget_ceiling=budget["ram"],
        candidates=candidates,
    )
    state.ram_name = result.ram_name
    state.thresholds["ram"] = result.reconsideration_threshold


async def _step_storage(state: DSPyBuildState, session: AsyncSession, budget: dict, program: DecideStorage, recorder: BuildRecorder | None) -> None:
    _emit(state, "storage", "Selecting storage…")
    candidates = await get_storage_candidates(
        session, budget["storage"], state.mobo_m2_slots, state.mobo_sata_ports,
    )
    result = await _run_step(
        recorder,
        program,
        status_fn=lambda msg: _emit(state, "storage", msg),
        use_cases=str(state.request.use_cases),
        budget_ceiling=budget["storage"],
        candidates=candidates,
    )
    state.storage_name = result.storage_name
    state.thresholds["storage"] = result.reconsideration_threshold


async def _step_gpu(state: DSPyBuildState, session: AsyncSession, budget: dict, program: DecideGPU, recorder: BuildRecorder | None) -> None:
    _emit(state, "gpu", "Finding your GPU…")
    candidates = await get_gpu_candidates(
        session, budget["gpu"], state.case_max_gpu_length_mm, state.request.preferences,
    )
    result = await _run_step(
        recorder,
        program,
        status_fn=lambda msg: _emit(state, "gpu", msg),
        use_cases=str(state.request.use_cases),
        budget_total=state.request.budget_usd,
        gpu_budget_ceiling=budget["gpu"],
        candidates=candidates,
    )
    state.gpu_required = result.gpu_required
    if state.gpu_required:
        state.gpu_name = result.gpu_name
        state.thresholds["gpu"] = result.reconsideration_threshold
        gpu = await crud_components.get_gpu_by_name(session, result.gpu_name)
        if gpu:
            state.gpu_tdp_w = gpu.tdp_watts


async def _step_psu(state: DSPyBuildState, session: AsyncSession, budget: dict, program: DecidePSU, recorder: BuildRecorder | None) -> None:
    _emit(state, "psu", "Calculating power supply…")
    # Add 20% headroom over combined TDP
    system_tdp = state.cpu_tdp_w + state.gpu_tdp_w
    min_wattage = int(system_tdp * 1.20)
    # Determine PSU form factor from case
    psu_form_factor = state.psu_form_factor  # updated after case step if ITX
    candidates = await get_psu_candidates(session, min_wattage, budget["psu"], psu_form_factor)
    result = await _run_step(
        recorder,
        program,
        status_fn=lambda msg: _emit(state, "psu", msg),
        required_wattage=min_wattage,
        budget_ceiling=budget["psu"],
        candidates=candidates,
    )
    state.psu_name = result.psu_name


async def _step_case(state: DSPyBuildState, session: AsyncSession, budget: dict, program: DecideCase, recorder: BuildRecorder | None) -> None:
    _emit(state, "case", "Picking case options for you…")
    candidates = await get_case_candidates(
        session, budget["case"], state.mobo_form_factor, state.psu_form_factor,
    )
    result = await _run_step(
        recorder,
        program,
        status_fn=lambda msg: _emit(state, "case", msg),
        use_cases=str(state.request.use_cases),
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


async def _step_fans(state: DSPyBuildState, session: AsyncSession, budget: dict, program: DecideFans, recorder: BuildRecorder | None) -> None:
    _emit(state, "fans", "Checking airflow…")
    candidates = await get_fan_candidates(session, budget["fans"], state.case_fan_slots)
    result = await _run_step(
        recorder,
        program,
        status_fn=lambda msg: _emit(state, "fans", msg),
        cpu_tdp_w=state.cpu_tdp_w,
        gpu_tdp_w=state.gpu_tdp_w,
        case_included_fans=state.case_included_fans,
        budget_ceiling=budget["fans"],
        candidates=candidates,
    )
    if result.fan_name.upper() != "NONE":
        state.fans_name = result.fan_name


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
    budget = _allocate_budget(request.budget_usd, request.use_cases)

    try:
        await _step_ddr(state, session, budget, load_ddr(), recorder)
        await _step_cpu(state, session, budget, load_cpu(), recorder)
        await _step_cooler(state, session, budget, load_cooler(), recorder)
        await _step_motherboard(state, session, budget, load_motherboard(), recorder)
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
    case = await crud_components.get_case_by_name(session, case_name)
    if case:
        state.case_included_fans = case.included_fan_count or 0
        state.case_max_gpu_length_mm = case.max_gpu_length_mm
        # Build fan slot list from available slots (e.g. three 120mm → [120, 120, 120])
        slot_size = 120  # default; cases don't store per-slot sizes separately
        total_slots = (case.max_fan_slots or 0) - state.case_included_fans
        state.case_fan_slots = [slot_size] * max(total_slots, 0)
    budget = _allocate_budget(state.request.budget_usd, state.request.use_cases)
    try:
        await _step_fans(state, session, budget, load_fans(), recorder)
        if recorder is not None:
            recorder.finish(BuildSessionStatus.COMPLETED)
    except Exception as exc:
        state.error = str(exc)
        if recorder is not None:
            recorder.finish(BuildSessionStatus.ERROR)
    return state
