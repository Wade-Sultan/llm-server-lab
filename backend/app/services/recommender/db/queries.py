"""
queries.py
==========
Fetches compatible part candidates from the DB using filters from filters.py.
Each function returns a list of plain dicts — the serialization format that
gets passed into DSPy modules as the `candidates` input field.

The `_serialize_*` helpers control exactly what the LLM sees per component type.
Keep these focused: only include fields that are actually useful for selection.
Price is always included; raw DB IDs are always excluded.
"""

from __future__ import annotations

import json

from sqlmodel import Session, select

from app.models.pcparts import PCPart
from app.services.recommender.db.filters import (
    case_filters,
    cpu_cooler_filters,
    cpu_filters,
    fan_filters,
    gpu_filters,
    motherboard_filters,
    psu_filters,
    ram_filters,
    storage_filters,
)
from app.schemas.chat import UserPreferences


def _run(session: Session, conditions: list) -> list[PCPart]:
    stmt = select(PCPart)
    for cond in conditions:
        stmt = stmt.where(cond)
    return list(session.exec(stmt).all())


def _price(part: PCPart) -> float | None:
    if part.street_price_cents is None:
        return None
    return round(part.street_price_cents / 100, 2)


# ---------------------------------------------------------------------------
# Serializers — one per component type.
# Edit these to include whatever spec fields live in your schema.
# ---------------------------------------------------------------------------

def _serialize_cpu(p: PCPart) -> dict:
    s = p.specs or {}
    return {
        "name": p.name,
        "brand": s.get("brand"),
        "cores": s.get("cores"),
        "threads": s.get("threads"),
        "base_clock_ghz": s.get("base_clock_ghz"),
        "boost_clock_ghz": s.get("boost_clock_ghz"),
        "tdp_w": s.get("tdp_w"),
        "socket": s.get("socket"),
        "ddr_gen": s.get("ddr_gen"),
        "has_integrated_graphics": s.get("has_integrated_graphics"),
        "street_price_usd": _price(p),
    }


def _serialize_cooler(p: PCPart) -> dict:
    s = p.specs or {}
    return {
        "name": p.name,
        "type": s.get("type"),           # air / aio_240 / aio_360 etc.
        "max_tdp_w": s.get("max_tdp_w"),
        "noise_db": s.get("noise_db"),
        "height_mm": s.get("height_mm"), # relevant for case clearance
        "street_price_usd": _price(p),
    }


def _serialize_motherboard(p: PCPart) -> dict:
    s = p.specs or {}
    return {
        "name": p.name,
        "socket": s.get("socket"),
        "chipset": s.get("chipset"),
        "form_factor": s.get("form_factor"),
        "ddr_gen": s.get("ddr_gen"),
        "ram_slots": s.get("ram_slots"),
        "m2_slots": s.get("m2_slots"),
        "sata_ports": s.get("sata_ports"),
        "has_wifi": s.get("has_wifi"),
        "supports_bios_flashback": s.get("supports_bios_flashback"),
        "vrm_quality": s.get("vrm_quality"),  # e.g. "entry" / "mid" / "high"
        "street_price_usd": _price(p),
    }


def _serialize_ram(p: PCPart) -> dict:
    s = p.specs or {}
    return {
        "name": p.name,
        "ddr_gen": s.get("ddr_gen"),
        "capacity_gb": s.get("capacity_gb"),
        "speed_mhz": s.get("speed_mhz"),
        "kit_count": s.get("kit_count"),  # 1x16, 2x8, 2x16, etc.
        "street_price_usd": _price(p),
    }


def _serialize_storage(p: PCPart) -> dict:
    s = p.specs or {}
    return {
        "name": p.name,
        "interface": s.get("interface"),  # pcie_gen4, pcie_gen5, sata3
        "capacity_gb": s.get("capacity_gb"),
        "seq_read_mbs": s.get("seq_read_mbs"),
        "seq_write_mbs": s.get("seq_write_mbs"),
        "street_price_usd": _price(p),
    }


def _serialize_gpu(p: PCPart) -> dict:
    s = p.specs or {}
    return {
        "name": p.name,
        "brand": s.get("brand"),
        "vram_gb": s.get("vram_gb"),
        "tdp_w": s.get("tdp_w"),
        "length_mm": s.get("length_mm"),
        "pcie_slots": s.get("pcie_slots"),
        "street_price_usd": _price(p),
        "used_market_viable": p.used_market_viable,
    }


def _serialize_psu(p: PCPart) -> dict:
    s = p.specs or {}
    return {
        "name": p.name,
        "wattage": s.get("wattage"),
        "efficiency": s.get("efficiency"),  # 80plus_gold etc.
        "modular": s.get("modular"),
        "form_factor": s.get("form_factor"),
        "street_price_usd": _price(p),
    }


def _serialize_case(p: PCPart) -> dict:
    s = p.specs or {}
    return {
        "name": p.name,
        "size": s.get("size"),
        "supported_mobo_sizes": s.get("supported_mobo_sizes"),
        "max_gpu_length_mm": s.get("max_gpu_length_mm"),
        "max_cooler_height_mm": s.get("max_cooler_height_mm"),
        "fan_slots": s.get("fan_slots"),
        "included_fans": s.get("included_fans"),
        "street_price_usd": _price(p),
    }


def _serialize_fan(p: PCPart) -> dict:
    s = p.specs or {}
    return {
        "name": p.name,
        "size_mm": s.get("size_mm"),
        "airflow_cfm": s.get("airflow_cfm"),
        "noise_db": s.get("noise_db"),
        "pack_count": s.get("pack_count"),
        "street_price_usd": _price(p),
    }


def _to_candidates_string(parts: list[PCPart], serializer) -> str:
    """Serialize a list of parts to a JSON string for DSPy input."""
    return json.dumps([serializer(p) for p in parts], indent=None)


# ---------------------------------------------------------------------------
# Public query functions
# ---------------------------------------------------------------------------

def get_cpu_candidates(
    session: Session,
    budget_ceiling_usd: int,
    preferences: UserPreferences,
) -> str:
    parts = _run(session, cpu_filters(budget_ceiling_usd, preferences))
    return _to_candidates_string(parts, _serialize_cpu)


def get_cooler_candidates(
    session: Session,
    cpu_tdp_w: int,
    cpu_socket: str,
    budget_ceiling_usd: int,
    form_factor: str,
) -> str:
    parts = _run(session, cpu_cooler_filters(cpu_tdp_w, cpu_socket, budget_ceiling_usd, form_factor))
    return _to_candidates_string(parts, _serialize_cooler)


def get_motherboard_candidates(
    session: Session,
    cpu_socket: str,
    ddr_gen: str,
    budget_ceiling_usd: int,
    form_factor: str,
    wifi_required: bool,
) -> str:
    parts = _run(session, motherboard_filters(cpu_socket, ddr_gen, budget_ceiling_usd, form_factor, wifi_required))
    return _to_candidates_string(parts, _serialize_motherboard)


def get_ram_candidates(
    session: Session,
    ddr_gen: str,
    budget_ceiling_usd: int,
) -> str:
    parts = _run(session, ram_filters(ddr_gen, budget_ceiling_usd))
    return _to_candidates_string(parts, _serialize_ram)


def get_storage_candidates(
    session: Session,
    budget_ceiling_usd: int,
    mobo_m2_slots: int,
    mobo_sata_ports: int,
) -> str:
    parts = _run(session, storage_filters(budget_ceiling_usd, mobo_m2_slots, mobo_sata_ports))
    return _to_candidates_string(parts, _serialize_storage)


def get_gpu_candidates(
    session: Session,
    budget_ceiling_usd: int,
    case_max_gpu_length_mm: int | None,
    preferences: UserPreferences,
) -> str:
    parts = _run(session, gpu_filters(budget_ceiling_usd, case_max_gpu_length_mm, preferences))
    return _to_candidates_string(parts, _serialize_gpu)


def get_psu_candidates(
    session: Session,
    min_wattage: int,
    budget_ceiling_usd: int,
    psu_form_factor: str,
) -> str:
    parts = _run(session, psu_filters(min_wattage, budget_ceiling_usd, psu_form_factor))
    return _to_candidates_string(parts, _serialize_psu)


def get_case_candidates(
    session: Session,
    budget_ceiling_usd: int,
    mobo_form_factor: str,
    psu_form_factor: str,
) -> str:
    parts = _run(session, case_filters(budget_ceiling_usd, mobo_form_factor, psu_form_factor))
    return _to_candidates_string(parts, _serialize_case)


def get_fan_candidates(
    session: Session,
    budget_ceiling_usd: int,
    case_fan_slots: list[int],
) -> str:
    parts = _run(session, fan_filters(budget_ceiling_usd, case_fan_slots))
    return _to_candidates_string(parts, _serialize_fan)