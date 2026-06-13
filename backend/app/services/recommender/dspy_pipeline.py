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

This pipeline is synchronous. Call it from FastAPI via run_in_executor or
a background task to avoid blocking the event loop.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable

import dspy
from sqlmodel import Session

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
from app.schemas.chat import BuildRequest


# ---------------------------------------------------------------------------
# DSPy configuration
# ---------------------------------------------------------------------------

def configure_dspy() -> None:
    """Configure DSPy with Haiku for inference. Call once at app startup."""
    lm = dspy.LM(
        model="anthropic/claude-haiku-4-5-20251001",
        api_key=os.environ["ANTHROPIC_API_KEY"],
        max_tokens=1024,
        temperature=0.3,
    )
    dspy.configure(lm=lm)


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

def _step_ddr(state: DSPyBuildState, session: Session, budget: dict, program: DecideDDR) -> None:
    _emit(state, "ddr", "Deciding memory generation…")
    candidates = get_ddr_candidates(session, budget["cpu"])
    result = program(
        use_cases=str(state.request.use_cases),
        budget_total=state.request.budget_usd,
        candidates=candidates,
    )
    state.cpu_ddr_gen = result.ddr_gen
    state.thresholds["ddr"] = result.reconsideration_threshold


def _step_cpu(state: DSPyBuildState, session: Session, budget: dict, program: DecideCPU) -> None:
    _emit(state, "cpu", "Choosing your CPU…")
    candidates = get_cpu_candidates(session, budget["cpu"], state.request.preferences)
    result = program(
        use_cases=str(state.request.use_cases),
        budget_total=state.request.budget_usd,
        cpu_budget_ceiling=budget["cpu"],
        candidates=candidates,
    )
    state.cpu_name = result.cpu_name
    state.thresholds["cpu"] = result.reconsideration_threshold
    # Extract socket + DDR gen from the DB for downstream steps
    # TODO: look up cpu_socket and cpu_ddr_gen from pc_parts by result.cpu_name
    #       and set state.cpu_socket, state.cpu_tdp_w, state.cpu_ddr_gen


def _step_cooler(state: DSPyBuildState, session: Session, budget: dict, program: DecideCPUCooler) -> None:
    _emit(state, "cooler", "Picking a cooler…")
    candidates = get_cooler_candidates(
        session, state.cpu_tdp_w, state.cpu_socket, budget["cooler"],
        state.request.preferences.form_factor,
    )
    result = program(
        use_cases=str(state.request.use_cases),
        cpu_name=state.cpu_name,
        cpu_tdp_w=state.cpu_tdp_w,
        budget_ceiling=budget["cooler"],
        candidates=candidates,
    )
    state.cooler_name = result.cooler_name
    state.thresholds["cooler"] = result.reconsideration_threshold


def _step_motherboard(state: DSPyBuildState, session: Session, budget: dict, program: DecideMotherboard) -> None:
    _emit(state, "motherboard", "Selecting a motherboard…")
    candidates = get_motherboard_candidates(
        session,
        cpu_socket=state.cpu_socket,
        ddr_gen=state.cpu_ddr_gen,
        budget_ceiling_usd=budget["mobo"],
        form_factor=state.request.preferences.form_factor,
        wifi_required=state.request.preferences.wifi_required,
    )
    result = program(
        use_cases=str(state.request.use_cases),
        cpu_name=state.cpu_name,
        ddr_gen=state.cpu_ddr_gen,
        budget_ceiling=budget["mobo"],
        candidates=candidates,
    )
    state.mobo_name = result.motherboard_name
    state.thresholds["motherboard"] = result.reconsideration_threshold
    # TODO: look up mobo_form_factor, mobo_m2_slots, mobo_sata_ports by name


def _step_ram(state: DSPyBuildState, session: Session, budget: dict, program: DecideRAM) -> None:
    _emit(state, "ram", "Choosing RAM…")
    candidates = get_ram_candidates(session, state.cpu_ddr_gen, budget["ram"])
    result = program(
        use_cases=str(state.request.use_cases),
        ddr_gen=state.cpu_ddr_gen,
        budget_ceiling=budget["ram"],
        candidates=candidates,
    )
    state.ram_name = result.ram_name
    state.thresholds["ram"] = result.reconsideration_threshold


def _step_storage(state: DSPyBuildState, session: Session, budget: dict, program: DecideStorage) -> None:
    _emit(state, "storage", "Selecting storage…")
    candidates = get_storage_candidates(
        session, budget["storage"], state.mobo_m2_slots, state.mobo_sata_ports,
    )
    result = program(
        use_cases=str(state.request.use_cases),
        budget_ceiling=budget["storage"],
        candidates=candidates,
    )
    state.storage_name = result.storage_name
    state.thresholds["storage"] = result.reconsideration_threshold


def _step_gpu(state: DSPyBuildState, session: Session, budget: dict, program: DecideGPU) -> None:
    _emit(state, "gpu", "Finding your GPU…")
    candidates = get_gpu_candidates(
        session, budget["gpu"], state.case_max_gpu_length_mm, state.request.preferences,
    )
    result = program(
        use_cases=str(state.request.use_cases),
        budget_total=state.request.budget_usd,
        gpu_budget_ceiling=budget["gpu"],
        candidates=candidates,
    )
    state.gpu_required = result.gpu_required
    if state.gpu_required:
        state.gpu_name = result.gpu_name
        state.thresholds["gpu"] = result.reconsideration_threshold
        # TODO: look up gpu_tdp_w by name


def _step_psu(state: DSPyBuildState, session: Session, budget: dict, program: DecidePSU) -> None:
    _emit(state, "psu", "Calculating power supply…")
    # Add 20% headroom over combined TDP
    system_tdp = state.cpu_tdp_w + state.gpu_tdp_w
    min_wattage = int(system_tdp * 1.20)
    # Determine PSU form factor from case
    psu_form_factor = state.psu_form_factor  # updated after case step if ITX
    candidates = get_psu_candidates(session, min_wattage, budget["psu"], psu_form_factor)
    result = program(
        required_wattage=min_wattage,
        budget_ceiling=budget["psu"],
        candidates=candidates,
    )
    state.psu_name = result.psu_name


def _step_case(state: DSPyBuildState, session: Session, budget: dict, program: DecideCase) -> None:
    _emit(state, "case", "Picking case options for you…")
    candidates = get_case_candidates(
        session, budget["case"], state.mobo_form_factor, state.psu_form_factor,
    )
    result = program(
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


def _step_fans(state: DSPyBuildState, session: Session, budget: dict, program: DecideFans) -> None:
    _emit(state, "fans", "Checking airflow…")
    candidates = get_fan_candidates(session, budget["fans"], state.case_fan_slots)
    result = program(
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

def run_pipeline(
    request: BuildRequest,
    session: Session,
    progress_callback: Callable[[str, str], None] | None = None,
) -> DSPyBuildState:
    """
    Run the full DSPy component-by-component pipeline.

    Returns a DSPyBuildState with all decisions populated up to (but not
    including) the case selection, which requires a round-trip to the user.
    Call run_pipeline_post_case() once the user has picked their case.
    """
    state = DSPyBuildState(request=request, progress_callback=progress_callback)
    budget = _allocate_budget(request.budget_usd, request.use_cases)

    try:
        _step_ddr(state, session, budget, load_ddr())
        _step_cpu(state, session, budget, load_cpu())
        _step_cooler(state, session, budget, load_cooler())
        _step_motherboard(state, session, budget, load_motherboard())
        _step_ram(state, session, budget, load_ram())
        _step_storage(state, session, budget, load_storage())
        _step_gpu(state, session, budget, load_gpu())
        _step_psu(state, session, budget, load_psu())
        _step_case(state, session, budget, load_case())
        # Pipeline pauses — return state with case_options populated
    except Exception as exc:
        state.error = str(exc)

    return state


def run_pipeline_post_case(
    state: DSPyBuildState,
    session: Session,
    case_name: str,
) -> DSPyBuildState:
    """
    Resume the pipeline after the user has selected a case.
    Runs the fans step and finalizes the build.
    """
    state.case_name = case_name
    # TODO: look up case_included_fans and case_fan_slots from pc_parts by case_name
    budget = _allocate_budget(state.request.budget_usd, state.request.use_cases)
    try:
        _step_fans(state, session, budget, load_fans())
    except Exception as exc:
        state.error = str(exc)
    return state