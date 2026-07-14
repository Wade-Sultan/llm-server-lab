"""
Unit tests for the DSPy pipeline steps in
app.services.recommender.dspy_pipeline.

Pure-logic tests: the DB layer (get_*_candidates queries + crud_components
lookups) and the LLM layer (_run_step, which normally runs a Decide* module
against OpenRouter) are all mocked, so nothing here needs Postgres or a network.
Each test drives one async step via asyncio.run and asserts on the resulting
DSPyBuildState — the compatibility wiring the recent work changed: DDR set
matching, socket carry-through, and the group→exact resolution for
GPU/RAM/Storage/PSU.
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


def _chipset(*, name="RTX 5080", tdp=300, rec_psu=None, vram=16, has_rt=True, price=120000):
    # Stands in for a GPUChipset (group) row — street price lives here now.
    return SimpleNamespace(
        id=name, name=name, tdp_watts=tdp, recommended_psu_watts=rec_psu,
        vram_gb=vram, has_ray_tracing=has_rt, street_price_cents=price,
    )


def _gpu(name, price_cents, *, chipset=None, length_mm=300, brand="nvidia",
         used_market_viable=False):
    # GPU exact (board): chipset intrinsic spec via .chipset, per-board length.
    return SimpleNamespace(
        name=name, chipset=chipset if chipset is not None else _chipset(),
        length_mm=length_mm, brand=brand, street_price_cents=price_cents,
        used_market_viable=used_market_viable,
    )


def _group(**fields):
    return SimpleNamespace(**fields)


def _exact(name, price_cents, group=None, *, used_market_viable=False):
    # RAM/Storage/PSU exact SKU: intrinsic spec via .group, price on the exact.
    return SimpleNamespace(
        name=name, group=group, street_price_cents=price_cents,
        used_market_viable=used_market_viable,
    )


def _async_return(value):
    async def _fn(*args, **kwargs):
        return value
    return _fn


def _patch_run_step(monkeypatch, prediction, capture: dict | None = None):
    async def _fake(recorder=None, program=None, *, status_fn=None, candidates=None, **inputs):
        if capture is not None:
            capture["candidates"] = candidates
            capture["inputs"] = inputs
        return prediction
    monkeypatch.setattr(dp, "_run_step", _fake)


# ---------------------------------------------------------------------------
# _cheapest_exact
# ---------------------------------------------------------------------------

def test_pick_exact_returns_first():
    # Price now lives on the group, so members are priced identically — the
    # resolver just returns the first active member.
    exacts = [_exact("a", 9000), _exact("b", 8000), _exact("c", 12000)]
    assert dp._pick_exact(exacts).name == "a"


def test_pick_exact_empty_returns_none():
    assert dp._pick_exact([]) is None


# ---------------------------------------------------------------------------
# _resolve_gpu_variant — chipset → exact board
# ---------------------------------------------------------------------------

def test_resolve_gpu_variant_picks_first_that_fits(monkeypatch):
    # Price is uniform across a chipset's boards, so resolution is first-that-fits.
    chip = _chipset(rec_psu=700)
    variants = [
        _gpu("MSI 5080", 120000, chipset=chip, length_mm=320),
        _gpu("Gigabyte 5080", 105000, chipset=chip, length_mm=300),
        _gpu("PNY 5080", 110000, chipset=chip, length_mm=290),
    ]
    monkeypatch.setattr(dp.crud_components, "get_gpus_for_chipset", _async_return(variants))
    monkeypatch.setattr(dp.crud_components, "get_psu_by_name",
                        _async_return(SimpleNamespace(name="psu", group=SimpleNamespace(wattage=850))))

    state = _state(gpu_required=True, gpu_chipset="RTX 5080",
                   psu_name="psu", case_max_gpu_length_mm=360)
    asyncio.run(dp._resolve_gpu_variant(state, object()))

    assert state.gpu_name == "MSI 5080"   # first board that fits
    assert state.gpu_tdp_w == 300         # chipset TDP pinned


def test_resolve_gpu_variant_excludes_too_long(monkeypatch):
    chip = _chipset(rec_psu=700)
    variants = [
        _gpu("cheap-but-long", 100000, chipset=chip, length_mm=380),
        _gpu("fits", 130000, chipset=chip, length_mm=300),
    ]
    monkeypatch.setattr(dp.crud_components, "get_gpus_for_chipset", _async_return(variants))
    monkeypatch.setattr(dp.crud_components, "get_psu_by_name",
                        _async_return(SimpleNamespace(name="psu", group=SimpleNamespace(wattage=850))))

    state = _state(gpu_required=True, gpu_chipset="RTX 5080",
                   psu_name="psu", case_max_gpu_length_mm=360)
    asyncio.run(dp._resolve_gpu_variant(state, object()))

    assert state.gpu_name == "fits"


def test_resolve_gpu_variant_underpowered_chipset_falls_back(monkeypatch):
    # recommended_psu_watts is chip-intrinsic (shared), so an underpowered PSU
    # excludes *every* board of the chipset → fall back to the cheapest one.
    chip = _chipset(rec_psu=1000)
    variants = [
        _gpu("A", 120000, chipset=chip, length_mm=300),
        _gpu("B", 100000, chipset=chip, length_mm=300),
    ]
    monkeypatch.setattr(dp.crud_components, "get_gpus_for_chipset", _async_return(variants))
    monkeypatch.setattr(dp.crud_components, "get_psu_by_name",
                        _async_return(SimpleNamespace(name="psu", group=SimpleNamespace(wattage=850))))

    state = _state(gpu_required=True, gpu_chipset="RTX 5080",
                   psu_name="psu", case_max_gpu_length_mm=360)
    asyncio.run(dp._resolve_gpu_variant(state, object()))

    assert state.gpu_name == "A"   # none fit → first variant


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
    chip = _chipset(rec_psu=2000)
    variants = [_gpu("long-cheap", 90000, chipset=chip, length_mm=999),
                _gpu("short-dear", 95000, chipset=chip, length_mm=200)]
    monkeypatch.setattr(dp.crud_components, "get_gpus_for_chipset", _async_return(variants))
    monkeypatch.setattr(dp.crud_components, "get_psu_by_name", _async_return(None))

    state = _state(gpu_required=True, gpu_chipset="RTX 5080",
                   psu_name="", case_max_gpu_length_mm=None)
    asyncio.run(dp._resolve_gpu_variant(state, object()))

    assert state.gpu_name == "long-cheap"


# ---------------------------------------------------------------------------
# _step_gpu — chooses a chipset, sizes PSU on chipset TDP
# ---------------------------------------------------------------------------

def test_step_gpu_sets_chipset_and_tdp(monkeypatch):
    monkeypatch.setattr(dp, "get_gpu_chipset_candidates",
                        _async_return('[{"chipset": "RTX 5080"}]'))
    _patch_run_step(monkeypatch, SimpleNamespace(
        gpu_chipset="RTX 5080", gpu_required=True, reconsideration_threshold="t"))
    chip = _chipset(tdp=340)
    monkeypatch.setattr(dp.crud_components, "get_gpus_for_chipset", _async_return([
        _gpu("MSI 5080", 120000, chipset=chip),
        _gpu("Gigabyte 5080", 105000, chipset=chip),
    ]))

    state = _state()
    asyncio.run(dp._step_gpu(state, object(), BUDGET, object(), None))

    assert state.gpu_chipset == "RTX 5080"
    assert state.gpu_tdp_w == 340       # from the chipset group
    assert state.gpu_name == ""         # exact board resolved later


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
# RAM / Storage / PSU — pick a group, resolve the cheapest exact
# ---------------------------------------------------------------------------

def test_step_ram_picks_group_and_resolves_first_kit(monkeypatch):
    monkeypatch.setattr(dp, "get_ram_candidates", _async_return('[{"ram_group": "G"}]'))
    _patch_run_step(monkeypatch, SimpleNamespace(
        ram_group="DDR5-6000 32GB (2x16)", reconsideration_threshold="t"))
    monkeypatch.setattr(dp.crud_components, "get_ram_kits_for_group", _async_return([
        _exact("Corsair kit", 9000), _exact("G.Skill kit", 8000),
    ]))

    state = _state(mobo_ddr_gen="ddr5")
    asyncio.run(dp._step_ram(state, object(), BUDGET, object(), None))

    assert state.ram_group == "DDR5-6000 32GB (2x16)"
    assert state.ram_name == "Corsair kit"   # first member (price is on the group)


def test_step_storage_picks_group_and_resolves_first_drive(monkeypatch):
    monkeypatch.setattr(dp, "get_storage_candidates", _async_return('[{"storage_group": "G"}]'))
    _patch_run_step(monkeypatch, SimpleNamespace(
        storage_group="2000GB pcie_gen4 nvme", reconsideration_threshold="t"))
    monkeypatch.setattr(dp.crud_components, "get_storage_drives_for_group", _async_return([
        _exact("WD SN850X", 15000), _exact("Crucial T500", 12000),
    ]))

    state = _state(mobo_m2_slots=2, mobo_sata_ports=4)
    asyncio.run(dp._step_storage(state, object(), BUDGET, object(), None))

    assert state.storage_group == "2000GB pcie_gen4 nvme"
    assert state.storage_name == "WD SN850X"


def test_step_psu_picks_group_and_resolves_first_unit(monkeypatch):
    monkeypatch.setattr(dp, "get_psu_candidates", _async_return('[{"psu_group": "G"}]'))
    _patch_run_step(monkeypatch, SimpleNamespace(psu_group="850W 80plus_gold atx", reason="r"))
    monkeypatch.setattr(dp.crud_components, "get_psus_for_group", _async_return([
        _exact("Corsair RM850x", 14000), _exact("MSI MAG A850", 11000),
    ]))

    state = _state(cpu_tdp_w=105, gpu_tdp_w=300)
    asyncio.run(dp._step_psu(state, object(), BUDGET, object(), None))

    assert state.psu_group == "850W 80plus_gold atx"
    assert state.psu_name == "Corsair RM850x"


# ---------------------------------------------------------------------------
# Group-candidate aggregation (queries.py) — one row per group
# ---------------------------------------------------------------------------

def test_gpu_chipset_candidates_aggregates_one_row_per_chipset(monkeypatch):
    chip_5080 = _chipset(name="RTX 5080", tdp=300, vram=16, price=105000)
    chip_5070 = _chipset(name="RTX 5070", tdp=220, vram=12, price=70000)
    rows_in = [
        _gpu("MSI 5080", 120000, chipset=chip_5080, used_market_viable=False),
        _gpu("Gigabyte 5080", 105000, chipset=chip_5080, used_market_viable=True),
        _gpu("ASUS 5070", 70000, chipset=chip_5070),
    ]
    monkeypatch.setattr(q.crud, "get_gpu_candidates", _async_return(rows_in))

    out = json.loads(asyncio.run(
        q.get_gpu_chipset_candidates(object(), 2000, UserPreferences())
    ))
    by = {r["chipset"]: r for r in out}

    assert set(by) == {"RTX 5080", "RTX 5070"}
    assert by["RTX 5080"]["street_price_usd"] == 1050.0    # from the chipset (group)
    assert by["RTX 5080"]["vram_gb"] == 16
    assert by["RTX 5080"]["brand"] == "nvidia"              # from a representative exact
    assert by["RTX 5080"]["used_market_viable"] is True     # OR-ed across boards
    assert [r["chipset"] for r in out] == ["RTX 5070", "RTX 5080"]   # cheapest-first


def test_ram_candidates_aggregates_one_row_per_group(monkeypatch):
    g = _group(id="g1", name="DDR5-6000 32GB (2x16)", ddr_generation="ddr5",
               capacity_gb=32, speed_mhz=6000, modules=2, cas_latency=30,
               street_price_cents=8000)
    rows_in = [_exact("Corsair", 9000, g), _exact("G.Skill", 8000, g)]
    monkeypatch.setattr(q.crud, "get_ram_candidates", _async_return(rows_in))

    out = json.loads(asyncio.run(q.get_ram_candidates(object(), "ddr5", 200)))

    assert len(out) == 1
    assert out[0]["ram_group"] == "DDR5-6000 32GB (2x16)"
    assert out[0]["capacity_gb"] == 32
    assert out[0]["street_price_usd"] == 80.0   # from the group


# ---------------------------------------------------------------------------
# _step_cpu — socket carry-through, DDR set, platform-pick reconciliation
# (CPU is not split; unchanged behaviour, kept as a regression guard)
# ---------------------------------------------------------------------------

def _prep_cpu(monkeypatch, cpu):
    monkeypatch.setattr(dp, "get_cpu_candidates", _async_return('[{"name": "x"}]'))
    _patch_run_step(monkeypatch, SimpleNamespace(
        cpu_name="AMD Ryzen 5 7600X", reconsideration_threshold="t"))
    monkeypatch.setattr(dp.crud_components, "get_cpu_by_name", _async_return(cpu))


def test_step_cpu_captures_full_ddr_set(monkeypatch):
    cpu = SimpleNamespace(socket="AM5", tdp_watts=105, ddr_generation=["ddr4", "ddr5"])
    _prep_cpu(monkeypatch, cpu)

    state = _state()
    asyncio.run(dp._step_cpu(state, object(), BUDGET, object(), None))

    assert state.cpu_socket == "AM5"
    assert state.cpu_ddr_gens == ["ddr4", "ddr5"]
    assert state.cpu_ddr_gen == "ddr5"   # platform pick supported → kept


def test_step_cpu_falls_back_when_platform_pick_unsupported(monkeypatch):
    cpu = SimpleNamespace(socket="AM5", tdp_watts=105, ddr_generation=["ddr5"])
    _prep_cpu(monkeypatch, cpu)

    state = _state(cpu_ddr_gen="ddr4")
    asyncio.run(dp._step_cpu(state, object(), BUDGET, object(), None))

    assert state.cpu_ddr_gen == "ddr5"


def test_step_cpu_raises_when_cpu_not_found(monkeypatch):
    _prep_cpu(monkeypatch, None)

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

    assert capture["ddr_gens"] == ["ddr4", "ddr5"]
    assert capture["cpu_socket"] == "AM5"
    assert state.mobo_ddr_gen == "ddr5"
    assert state.mobo_form_factor == "atx"


def test_step_ram_uses_chosen_board_generation(monkeypatch):
    capture: dict = {}

    async def _fake_candidates(session, ddr_gen, budget_ceiling_usd):
        capture["ddr_gen"] = ddr_gen
        return '[{"ram_group": "G"}]'
    monkeypatch.setattr(dp, "get_ram_candidates", _fake_candidates)
    _patch_run_step(monkeypatch, SimpleNamespace(ram_group="G", reconsideration_threshold="t"))
    monkeypatch.setattr(dp.crud_components, "get_ram_kits_for_group", _async_return([_exact("kit", 8000)]))

    # CPU platform gen is ddr4 but the chosen board is ddr5 → RAM follows the board.
    state = _state(cpu_ddr_gen="ddr4", mobo_ddr_gen="ddr5")
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
