"""
_assemble_dspy_build: turning a finished pipeline state into the BuildCard
payload, now that a state can carry several GPUs, drives and fans.

The arithmetic is the point. approx_price is per unit (matching
pc_build_parts.price_at_build), so a total that forgets the multiplier
under-reports a four-GPU build by three cards — a silent four-figure error in
a number the user is asked to spend against.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.services import chat_pipeline as cp

_PRICES = {
    "Ryzen 9 9950X": 55_000,
    "NH-D15": 11_000,
    "TRX50 board": 85_000,
    "128GB DDR5 RDIMM": 60_000,
    "WD SN850X 2TB": 15_000,
    "Seagate Exos 16TB": 25_000,
    "RTX 5090 FE": 200_000,
    "1600W Titanium": 60_000,
    "Meshify 2 XL": 20_000,
    "Noctua NF-A12x25": 3_000,
}


def _state(**overrides):
    base = {
        "cpu_name": "Ryzen 9 9950X",
        "cooler_name": "NH-D15",
        "mobo_name": "TRX50 board",
        "ram_name": "128GB DDR5 RDIMM",
        "storage_names": ["WD SN850X 2TB"],
        "gpu_name": "RTX 5090 FE",
        "gpu_count": 1,
        "psu_name": "1600W Titanium",
        "case_name": "Meshify 2 XL",
        "fans_name": "",
        "fans_quantity": 1,
    }
    return SimpleNamespace(**{**base, **overrides})


@pytest.fixture
def db(monkeypatch):
    """Stub the two catalog lookups _assemble_dspy_build makes."""

    async def _get_part_by_name(_db, name):
        if name not in _PRICES:
            return None
        return SimpleNamespace(id=uuid.uuid4(), manufacturer="ACME", name=name)

    async def _resolve_price(_db, part):
        return _PRICES.get(part.name)

    monkeypatch.setattr("app.crud.components.get_part_by_name", _get_part_by_name)
    monkeypatch.setattr("app.crud.components.resolve_part_price_cents", _resolve_price)
    return object()


def _build(state, db):
    return asyncio.run(cp._assemble_dspy_build(state, db))


def _by_component(build, component):
    return [p for p in build["parts"] if p["component"] == component]


# ---------------------------------------------------------------------------


def test_single_part_build_is_unchanged(db):
    build = _build(_state(), db)
    assert len(_by_component(build, "GPU")) == 1
    assert _by_component(build, "GPU")[0]["quantity"] == 1
    expected = sum(
        _PRICES[n]
        for n in [
            "Ryzen 9 9950X",
            "NH-D15",
            "TRX50 board",
            "128GB DDR5 RDIMM",
            "WD SN850X 2TB",
            "RTX 5090 FE",
            "1600W Titanium",
            "Meshify 2 XL",
        ]
    )
    assert build["total_approx"] == expected


def test_four_gpus_are_one_line_priced_four_times(db):
    """Identical cards collapse to one row with a quantity, mirroring
    BuildPart — but the total still counts all four."""
    one = _build(_state(), db)["total_approx"]
    four = _build(_state(gpu_count=4), db)["total_approx"]

    gpu_lines = _by_component(_build(_state(gpu_count=4), db), "GPU")
    assert len(gpu_lines) == 1
    assert gpu_lines[0]["quantity"] == 4
    # approx_price stays per unit so the card can show "4 x $2,000".
    assert gpu_lines[0]["approx_price"] == _PRICES["RTX 5090 FE"]
    assert four - one == _PRICES["RTX 5090 FE"] * 3


def test_multiple_drives_are_separate_lines(db):
    """Storage members differ from each other, so they are distinct rows rather
    than a quantity — the same split BuildPart makes."""
    build = _build(_state(storage_names=["WD SN850X 2TB", "Seagate Exos 16TB"]), db)
    drives = _by_component(build, "Storage")
    assert [d["model"] for d in drives] == ["WD SN850X 2TB", "Seagate Exos 16TB"]
    assert all(d["quantity"] == 1 for d in drives)


def test_fan_quantity_multiplies_the_total(db):
    base = _build(_state(), db)["total_approx"]
    withfans = _build(_state(fans_name="Noctua NF-A12x25", fans_quantity=3), db)[
        "total_approx"
    ]
    assert withfans - base == _PRICES["Noctua NF-A12x25"] * 3


def test_empty_and_unresolvable_parts_are_skipped(db):
    """An empty slot contributes no line; a name the catalog can't match still
    gets a line (so the user sees what was picked) but no price."""
    build = _build(_state(gpu_name="", storage_names=["Nonexistent Drive"]), db)
    assert _by_component(build, "GPU") == []
    drives = _by_component(build, "Storage")
    assert len(drives) == 1
    assert drives[0]["approx_price"] is None
    assert drives[0]["part_id"] == ""


def test_a_missing_quantity_attribute_defaults_to_one(db):
    """States built before the multi-instance fields existed (or by a partial
    failure) must not crash or zero out the total."""
    state = _state()
    del state.gpu_count
    build = _build(state, db)
    assert _by_component(build, "GPU")[0]["quantity"] == 1


def test_recommendation_prompt_shows_the_multiplier(db):
    """Without this the lead-in would describe a four-GPU server as if it had
    one card."""
    build = _build(_state(gpu_count=4), db)
    profile = cp.BuildProfile(primary_use="server", budget_tier="custom")
    text = cp._format_build_context(profile, "custom", build)
    assert "4x ACME RTX 5090 FE" in text
    # Line total, not unit price.
    assert f"~${_PRICES['RTX 5090 FE'] * 4 / 100:.0f}" in text


def test_recommendation_prompt_omits_the_multiplier_for_single_parts(db):
    build = _build(_state(), db)
    profile = cp.BuildProfile(primary_use="gaming", budget_tier="mid")
    text = cp._format_build_context(profile, "custom", build)
    assert "1x " not in text
