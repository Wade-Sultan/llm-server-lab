"""
Unit tests for the DSPy pipeline steps in
app.services.recommender.dspy_pipeline.

These are pure-logic tests: the DB layer (the get_*_candidates queries and the
crud_components lookups) and the LLM layer (_run_step, which normally runs a
Decide* module against OpenRouter) are all mocked, so nothing here needs
Postgres or a network. Each test drives one async step via asyncio.run and
asserts on the resulting DSPyBuildState — i.e. the compatibility wiring that the
recent bugs lived in (ddr set matching, socket carry-through, the GPU
chipset → exact-board resolution).
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.schemas.chat import BuildRequest, UserPreferences
from app.services.recommender import dspy_pipeline as dp
from app.services.recommender.db import queries as q


# ---------------------------------------------------------------------------
# Fakes / helpers
# ---------------------------------------------------------------------------

BUDGET = {
    "cpu": 300, "cooler": 80, "mobo": 200, "ram": 120, "storage": 120,
    "gpu": 600, "psu": 120, "case": 120, "fans": 40,
}


def _state(**overrides) -> dp.DSPyBuildState:
    state = dp.DSPyBuildState(request=BuildRequest(use_cases=["gaming"], budget_usd=1500))
    for k, v in overrides.items():
        setattr(state, k, v)
    return state


def _gpu(name, price_cents, *, chipset="RTX 5080", length_mm=300,
         recommended_psu_watts=None, tdp_watts=250, vram_gb=16, brand="nvidia",
         used_market_viable=False):
    return SimpleNamespace(
        name=name, chipset=chipset, length_mm=length_mm,
        recommended_psu_watts=recommended_psu_watts, tdp_watts=tdp_watts,
        vram_gb=vram_gb, brand=brand, street_price_cents=price_cents,
        used_market_viable=used_market_viable,
    )


def _async_return(value):
    async def _fn(*args, **kwargs):
        return value
    return _fn


def _patch_run_step(monkeypatch, prediction, capture: dict | None = None):
    """Replace _run_step with a stub that skips the LLM and returns `prediction`.
    If `capture` is given, the kwargs the step passed are recorded into it."""
    async def _fake(recorder=None, program=None, *, status_fn=None, candidates=None, **inputs):
        if capture is not None:
            capture["candidates"] = candidates
            capture["inputs"] = inputs
        return prediction
    monkeypatch.setattr(dp, "_run_step", _fake)


# ---------------------------------------------------------------------------
# _resolve_gpu_variant — the headline: chipset → exact board
# ---------------------------------------------------------------------------

def test_resolve_gpu_variant_picks_cheapest_that_fits(monkeypatch):
    # Three RTX 5080 boards; all fit the case/PSU, so the cheapest wins.
    variants = [
        _gpu("MSI 5080", 120000, length_mm=320, tdp_watts=300),
        _gpu("Gigabyte 5080", 105000, length_mm=300, tdp_watts=280),
        _gpu("PNY 5080", 110000, length_mm=290, tdp_watts=270),
    ]
    monkeypatch.setattr(dp.crud_components, "get_gpus_for_chipset", _async_return(variants))
    monkeypatch.setattr(dp.crud_components, "get_psu_by_name",
                        _async_return(SimpleNamespace(name="Corsair 850", wattage=850)))

    state = _state(gpu_required=True, gpu_chipset="RTX 5080",
                   psu_name="Corsair 850", case_max_gpu_length_mm=360)
    asyncio.run(dp._resolve_gpu_variant(state, object()))

    assert state.gpu_name == "Gigabyte 5080"      # cheapest of the three
    assert state.gpu_tdp_w == 280                 # exact board's TDP now pinned


def test_resolve_gpu_variant_excludes_too_long_and_underpowered(monkeypatch):
    # Cheapest board is too long; next-cheapest needs more PSU than we have;
    # the remaining (pricier) board is the only compatible one.
    variants = [
        _gpu("Cheap-but-long", 100000, length_mm=380, recommended_psu_watts=700),
        _gpu("Cheap-but-thirsty", 105000, length_mm=300, recommended_psu_watts=1000),
        _gpu("Fits", 130000, length_mm=300, recommended_psu_watts=750),
    ]
    monkeypatch.setattr(dp.crud_components, "get_gpus_for_chipset", _async_return(variants))
    monkeypatch.setattr(dp.crud_components, "get_psu_by_name",
                        _async_return(SimpleNamespace(name="psu", wattage=850)))

    state = _state(gpu_required=True, gpu_chipset="RTX 5080",
                   psu_name="psu", case_max_gpu_length_mm=360)
    asyncio.run(dp._resolve_gpu_variant(state, object()))

    assert state.gpu_name == "Fits"


def test_resolve_gpu_variant_falls_back_to_cheapest_when_none_fit(monkeypatch):
    # Every board is too long for the tiny case → fall back to the cheapest one
    # rather than hard-failing (reference build stays the safety net).
    variants = [
        _gpu("A", 120000, length_mm=320),
        _gpu("B", 100000, length_mm=330),
    ]
    monkeypatch.setattr(dp.crud_components, "get_gpus_for_chipset", _async_return(variants))
    monkeypatch.setattr(dp.crud_components, "get_psu_by_name",
                        _async_return(SimpleNamespace(name="psu", wattage=850)))

    state = _state(gpu_required=True, gpu_chipset="RTX 5080",
                   psu_name="psu", case_max_gpu_length_mm=200)
    asyncio.run(dp._resolve_gpu_variant(state, object()))

    assert state.gpu_name == "B"   # cheapest overall


def test_resolve_gpu_variant_skips_when_not_required(monkeypatch):
    called = {"n": 0}

    async def _should_not_run(*a, **k):
        called["n"] += 1
        return []
    monkeypatch.setattr(dp.crud_components, "get_gpus_for_chipset", _should_not_run)

    state = _state(gpu_required=False, gpu_chipset="", gpu_name="")
    asyncio.run(dp._resolve_gpu_variant(state, object()))

    assert state.gpu_name == ""
    assert called["n"] == 0


def test_resolve_gpu_variant_no_constraints_when_case_and_psu_unknown(monkeypatch):
    # No PSU resolved and no case length → power/length filters are skipped and
    # the cheapest board of the chipset is chosen unconditionally.
    variants = [_gpu("Long-cheap", 90000, length_mm=999, recommended_psu_watts=2000),
                _gpu("Short-dear", 95000, length_mm=200)]
    monkeypatch.setattr(dp.crud_components, "get_gpus_for_chipset", _async_return(variants))
    monkeypatch.setattr(dp.crud_components, "get_psu_by_name", _async_return(None))

    state = _state(gpu_required=True, gpu_chipset="RTX 5080",
                   psu_name="", case_max_gpu_length_mm=None)
    asyncio.run(dp._resolve_gpu_variant(state, object()))

    assert state.gpu_name == "Long-cheap"


# ---------------------------------------------------------------------------
# get_gpu_chipset_candidates — aggregation for the main GPU step
# ---------------------------------------------------------------------------

def test_gpu_chipset_candidates_aggregates_one_row_per_chipset(monkeypatch):
    rows = [
        _gpu("MSI 5080", 120000, chipset="RTX 5080", tdp_watts=300, vram_gb=16,
             used_market_viable=False),
        _gpu("Gigabyte 5080", 105000, chipset="RTX 5080", tdp_watts=340, vram_gb=16,
             used_market_viable=True),
        _gpu("ASUS 5070", 70000, chipset="RTX 5070", tdp_watts=220, vram_gb=12),
    ]
    monkeypatch.setattr(q.crud, "get_gpu_candidates", _async_return(rows))

    out = json.loads(asyncio.run(
        q.get_gpu_chipset_candidates(object(), 2000, UserPreferences())
    ))

    by = {r["chipset"]: r for r in out}
    assert set(by) == {"RTX 5080", "RTX 5070"}
    # cheapest board price surfaces as the starting price
    assert by["RTX 5080"]["starting_price_usd"] == 1050.0
    # representative TDP is the max across boards (PSU sizing headroom)
    assert by["RTX 5080"]["tdp_w"] == 340
    # used_market_viable is OR-ed across variants
    assert by["RTX 5080"]["used_market_viable"] is True
    # sorted cheapest-first
    assert [r["chipset"] for r in out] == ["RTX 5070", "RTX 5080"]


# ---------------------------------------------------------------------------
# _step_gpu — chooses a chipset, sizes PSU on the max-TDP board
# ---------------------------------------------------------------------------

def test_step_gpu_sets_chipset_and_representative_tdp(monkeypatch):
    monkeypatch.setattr(dp, "get_gpu_chipset_candidates",
                        _async_return('[{"chipset": "RTX 5080"}]'))
    _patch_run_step(monkeypatch, SimpleNamespace(
        gpu_chipset="RTX 5080", gpu_required=True, reconsideration_threshold="t"))
    monkeypatch.setattr(dp.crud_components, "get_gpus_for_chipset", _async_return([
        _gpu("MSI 5080", 120000, tdp_watts=300),
        _gpu("Gigabyte 5080", 105000, tdp_watts=340),
    ]))

    state = _state()
    asyncio.run(dp._step_gpu(state, object(), BUDGET, object(), None))

    assert state.gpu_chipset == "RTX 5080"
    assert state.gpu_tdp_w == 340       # max across the chipset's boards
    assert state.gpu_name == ""         # exact board not resolved yet


def test_step_gpu_not_required_leaves_chipset_empty(monkeypatch):
    monkeypatch.setattr(dp, "get_gpu_chipset_candidates",
                        _async_return('[{"chipset": "RTX 5080"}]'))
    _patch_run_step(monkeypatch, SimpleNamespace(
        gpu_chipset="RTX 5080", gpu_required=False, reconsideration_threshold="t"))

    state = _state()
    asyncio.run(dp._step_gpu(state, object(), BUDGET, object(), None))

    assert state.gpu_required is False
    assert state.gpu_chipset == ""


# ---------------------------------------------------------------------------
# _step_cpu — socket carry-through, DDR set, platform-pick reconciliation
# ---------------------------------------------------------------------------

def _prep_cpu(monkeypatch, cpu):
    monkeypatch.setattr(dp, "get_cpu_candidates", _async_return('[{"name": "x"}]'))
    _patch_run_step(monkeypatch, SimpleNamespace(
        cpu_name="AMD Ryzen 5 7600X", reconsideration_threshold="t"))
    monkeypatch.setattr(dp.crud_components, "get_cpu_by_name", _async_return(cpu))


def test_step_cpu_captures_full_ddr_set(monkeypatch):
    cpu = SimpleNamespace(socket="AM5", tdp_watts=105, ddr_generation=["ddr4", "ddr5"])
    _prep_cpu(monkeypatch, cpu)

    state = _state()  # cpu_ddr_gen default "ddr5"
    asyncio.run(dp._step_cpu(state, object(), BUDGET, object(), None))

    assert state.cpu_socket == "AM5"
    assert state.cpu_tdp_w == 105
    assert state.cpu_ddr_gens == ["ddr4", "ddr5"]
    # DDR platform pick "ddr5" is supported → kept as-is
    assert state.cpu_ddr_gen == "ddr5"


def test_step_cpu_falls_back_when_platform_pick_unsupported(monkeypatch):
    cpu = SimpleNamespace(socket="AM5", tdp_watts=105, ddr_generation=["ddr5"])
    _prep_cpu(monkeypatch, cpu)

    state = _state(cpu_ddr_gen="ddr4")  # DDR step picked ddr4, CPU doesn't support it
    asyncio.run(dp._step_cpu(state, object(), BUDGET, object(), None))

    assert state.cpu_ddr_gen == "ddr5"  # fell back to the CPU's newest supported gen


def test_step_cpu_raises_when_cpu_not_found(monkeypatch):
    _prep_cpu(monkeypatch, None)  # tolerant lookup still missed

    state = _state()
    with pytest.raises(RuntimeError):
        asyncio.run(dp._step_cpu(state, object(), BUDGET, object(), None))


# ---------------------------------------------------------------------------
# _step_motherboard — passes the CPU's full DDR set, records the board's gen
# ---------------------------------------------------------------------------

def test_step_motherboard_passes_ddr_set_and_records_board_gen(monkeypatch):
    capture: dict = {}

    async def _fake_candidates(session, *, cpu_socket, ddr_gens, budget_ceiling_usd,
                               form_factor, wifi_required):
        capture["ddr_gens"] = ddr_gens
        capture["cpu_socket"] = cpu_socket
        return '[{"name": "B650 board"}]'
    monkeypatch.setattr(dp, "get_motherboard_candidates", _fake_candidates)
    _patch_run_step(monkeypatch, SimpleNamespace(
        motherboard_name="B650 board", reconsideration_threshold="t"))
    monkeypatch.setattr(dp.crud_components, "get_motherboard_by_name", _async_return(
        SimpleNamespace(form_factor="atx", ddr_generation="ddr5", m2_slots=2, sata_ports=4)))

    state = _state(cpu_socket="AM5", cpu_ddr_gens=["ddr4", "ddr5"])
    asyncio.run(dp._step_motherboard(state, object(), BUDGET, object(), None))

    # the whole supported set is offered to the query, not a single gen
    assert capture["ddr_gens"] == ["ddr4", "ddr5"]
    assert capture["cpu_socket"] == "AM5"
    # the chosen board's gen is recorded so RAM can match it
    assert state.mobo_ddr_gen == "ddr5"
    assert state.mobo_form_factor == "atx"


# ---------------------------------------------------------------------------
# _step_ram — matches the chosen board's generation, not the CPU's whole set
# ---------------------------------------------------------------------------

def test_step_ram_uses_chosen_board_generation(monkeypatch):
    capture: dict = {}

    async def _fake_candidates(session, ddr_gen, budget_ceiling_usd):
        capture["ddr_gen"] = ddr_gen
        return '[{"name": "DDR5 kit"}]'
    monkeypatch.setattr(dp, "get_ram_candidates", _fake_candidates)
    _patch_run_step(monkeypatch, SimpleNamespace(
        ram_name="DDR5 kit", reconsideration_threshold="t"))

    # CPU platform gen is ddr4, but the actually-chosen board is ddr5 → RAM must
    # follow the board.
    state = _state(cpu_ddr_gen="ddr4", mobo_ddr_gen="ddr5")
    asyncio.run(dp._step_ram(state, object(), BUDGET, object(), None))

    assert capture["ddr_gen"] == "ddr5"
    assert state.ram_name == "DDR5 kit"


def test_step_ram_falls_back_to_cpu_gen_when_board_gen_missing(monkeypatch):
    capture: dict = {}

    async def _fake_candidates(session, ddr_gen, budget_ceiling_usd):
        capture["ddr_gen"] = ddr_gen
        return '[{"name": "kit"}]'
    monkeypatch.setattr(dp, "get_ram_candidates", _fake_candidates)
    _patch_run_step(monkeypatch, SimpleNamespace(
        ram_name="kit", reconsideration_threshold="t"))

    state = _state(cpu_ddr_gen="ddr5", mobo_ddr_gen="")  # board lookup had missed
    asyncio.run(dp._step_ram(state, object(), BUDGET, object(), None))

    assert capture["ddr_gen"] == "ddr5"


# ---------------------------------------------------------------------------
# _ensure_candidates — empty candidate list fails the step
# ---------------------------------------------------------------------------

def test_step_motherboard_empty_candidates_raises(monkeypatch):
    monkeypatch.setattr(dp, "get_motherboard_candidates", _async_return("[]"))

    state = _state(cpu_socket="AM5", cpu_ddr_gens=["ddr5"])
    with pytest.raises(dp.NoValidCandidatesError):
        asyncio.run(dp._step_motherboard(state, object(), BUDGET, object(), None))
