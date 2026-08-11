"""
Integration tests for the dominance gate inside the pipeline steps.

test_scoring.py covers when find_dominant should and should not fire. These
cover the wiring around it: that a fired gate actually prevents the LLM call,
that state ends up identical to the LLM path, that the decision is still
recorded, and — most importantly — that the GPU step declines to use the gate
whenever the two questions it cannot answer (`gpu_required` and `gpu_count`)
are still live.

The DB and LLM layers are mocked throughout, same as test_pipeline_steps.py.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.schemas.chat import BuildRequest, UserPreferences
from app.services.recommender import dspy_pipeline as dp

BUDGET = {"cpu": 400, "gpu": 900}


def _state(**overrides) -> dp.DSPyBuildState:
    state = dp.DSPyBuildState(
        request=BuildRequest(
            use_cases=["gaming"], budget_usd=2000, preferences=UserPreferences()
        )
    )
    for k, v in overrides.items():
        setattr(state, k, v)
    return state


def _async_return(value):
    async def _fn(*args, **kwargs):
        return value

    return _fn


def _explode_if_called(monkeypatch):
    """Make _run_step a hard failure — the gate must not reach the LLM."""

    async def _fake(*args, **kwargs):
        raise AssertionError("_run_step was called; the dominance gate did not fire")

    monkeypatch.setattr(dp, "_run_step", _fake)


def _record_run_step(monkeypatch, prediction):
    """Let _run_step succeed, recording that it ran."""
    called = {"count": 0}

    async def _fake(*args, **kwargs):
        called["count"] += 1
        return prediction

    monkeypatch.setattr(dp, "_run_step", _fake)
    return called


# Two CPUs where one is both cheaper and materially faster — the shape the gate
# exists for.
_DOMINANT_CPUS = json.dumps(
    [
        {"name": "Ryzen 5 9600X", "street_price_usd": 200, "perf_score": 1.0},
        {"name": "Ryzen 5 7600", "street_price_usd": 300, "perf_score": 0.7},
    ]
)

_DOMINANT_GPUS = json.dumps(
    [
        {"chipset": "RTX 5070 Ti", "street_price_usd": 750, "perf_score": 1.0},
        {"chipset": "RTX 5060", "street_price_usd": 900, "perf_score": 0.65},
    ]
)


class _Recorder:
    """Captures record_deterministic_decision calls."""

    def __init__(self) -> None:
        self.deterministic: list[dict] = []

    def record_deterministic_decision(self, **kwargs):
        self.deterministic.append(kwargs)


# ---------------------------------------------------------------------------
# CPU step
# ---------------------------------------------------------------------------


def test_cpu_step_skips_the_llm_when_a_candidate_dominates(monkeypatch):
    monkeypatch.setattr(dp, "get_cpu_candidates", _async_return(_DOMINANT_CPUS))
    _explode_if_called(monkeypatch)
    monkeypatch.setattr(
        dp.crud_components,
        "get_cpu_by_name",
        _async_return(
            SimpleNamespace(
                socket="AM5", tdp_watts=65, pcie_lanes=28, ddr_generation=["ddr5"]
            )
        ),
    )

    state = _state()
    asyncio.run(dp._step_cpu(state, object(), BUDGET, SimpleNamespace(), None))

    assert state.cpu_name == "Ryzen 5 9600X"
    # State must be populated exactly as the LLM path would leave it.
    assert state.cpu_socket == "AM5"
    assert state.cpu_tdp_w == 65
    assert state.thresholds["cpu"]


def test_cpu_step_records_the_skipped_decision(monkeypatch):
    """A missing cpu row in module_decisions would read as a pipeline failure."""
    monkeypatch.setattr(dp, "get_cpu_candidates", _async_return(_DOMINANT_CPUS))
    _explode_if_called(monkeypatch)
    monkeypatch.setattr(
        dp.crud_components,
        "get_cpu_by_name",
        _async_return(
            SimpleNamespace(
                socket="AM5", tdp_watts=65, pcie_lanes=0, ddr_generation=["ddr5"]
            )
        ),
    )

    recorder = _Recorder()
    program = SimpleNamespace(
        category="cpu", signature_name="DecideCPU", signature_version=1
    )
    asyncio.run(dp._step_cpu(_state(), object(), BUDGET, program, recorder))

    assert len(recorder.deterministic) == 1
    entry = recorder.deterministic[0]
    assert entry["category"] == "cpu"
    assert entry["chosen_name"] == "Ryzen 5 9600X"
    assert entry["output_decision"]["dominance_margin"] > 0


def test_cpu_step_falls_through_to_the_llm_without_a_dominant_candidate(monkeypatch):
    """More performance for more money is a tradeoff the model must weigh."""
    candidates = json.dumps(
        [
            {"name": "Fast", "street_price_usd": 500, "perf_score": 1.0},
            {"name": "Cheap", "street_price_usd": 200, "perf_score": 0.7},
        ]
    )
    monkeypatch.setattr(dp, "get_cpu_candidates", _async_return(candidates))
    called = _record_run_step(
        monkeypatch,
        SimpleNamespace(cpu_name="Fast", reconsideration_threshold="t"),
    )
    monkeypatch.setattr(
        dp.crud_components,
        "get_cpu_by_name",
        _async_return(
            SimpleNamespace(
                socket="AM5", tdp_watts=105, pcie_lanes=0, ddr_generation=["ddr5"]
            )
        ),
    )

    state = _state()
    asyncio.run(dp._step_cpu(state, object(), BUDGET, SimpleNamespace(), None))

    assert called["count"] == 1
    assert state.cpu_name == "Fast"


# ---------------------------------------------------------------------------
# GPU step — the extra eligibility rules
# ---------------------------------------------------------------------------


def test_gpu_step_skips_the_llm_on_a_single_slot_gaming_board(monkeypatch):
    monkeypatch.setattr(dp, "get_gpu_chipset_candidates", _async_return(_DOMINANT_GPUS))
    _explode_if_called(monkeypatch)
    monkeypatch.setattr(
        dp.crud_components,
        "get_gpus_for_chipset",
        _async_return([SimpleNamespace(chipset=SimpleNamespace(tdp_watts=300))]),
    )

    state = _state(mobo_pcie_x16_slots=1)
    asyncio.run(dp._step_gpu(state, object(), BUDGET, SimpleNamespace(), None))

    assert state.gpu_chipset == "RTX 5070 Ti"
    assert state.gpu_required is True
    assert state.gpu_count == 1
    assert state.gpu_tdp_w == 300


def test_gpu_step_uses_the_llm_when_the_board_has_multiple_x16_slots(monkeypatch):
    """gpu_count is a live decision on a multi-slot board; the gate can't make it."""
    monkeypatch.setattr(dp, "get_gpu_chipset_candidates", _async_return(_DOMINANT_GPUS))
    called = _record_run_step(
        monkeypatch,
        SimpleNamespace(
            gpu_chipset="RTX 5070 Ti",
            gpu_count=2,
            gpu_required=True,
            reconsideration_threshold="t",
        ),
    )
    monkeypatch.setattr(
        dp.crud_components,
        "get_gpus_for_chipset",
        _async_return([SimpleNamespace(chipset=SimpleNamespace(tdp_watts=300))]),
    )

    state = _state(mobo_pcie_x16_slots=2)
    asyncio.run(dp._step_gpu(state, object(), BUDGET, SimpleNamespace(), None))

    assert called["count"] == 1
    assert state.gpu_count == 2


@pytest.mark.parametrize("use_case", ["productivity", "dev", "audio", "nas"])
def test_gpu_step_uses_the_llm_when_igpu_might_suffice(monkeypatch, use_case):
    """'No discrete GPU at all' is a live answer for these, and no amount of
    benchmark leadership among discrete cards can rule it out."""
    monkeypatch.setattr(dp, "get_gpu_chipset_candidates", _async_return(_DOMINANT_GPUS))
    called = _record_run_step(
        monkeypatch,
        SimpleNamespace(
            gpu_chipset="",
            gpu_count=1,
            gpu_required=False,
            reconsideration_threshold="t",
        ),
    )

    state = _state(mobo_pcie_x16_slots=1)
    state.request.use_cases = [use_case]
    asyncio.run(dp._step_gpu(state, object(), BUDGET, SimpleNamespace(), None))

    assert called["count"] == 1
    assert state.gpu_required is False
