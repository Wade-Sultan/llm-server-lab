"""
queries.py
==========
Fetches compatible part candidates via the CRUD layer and serializes them to
JSON strings for DSPy module inputs.

Each public function returns a JSON string — the format DSPy expects for the
`candidates` input field.  The `_serialize_*` helpers control exactly what
fields the LLM sees.  Price is always included; raw DB IDs are always excluded.
"""

from __future__ import annotations

import json
from collections import defaultdict

from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import components as crud
from app.models.pcparts import CPU, GPU, Case, CPUCooler, Fan, Motherboard, PSU, RAM, Storage
from app.schemas.chat import UserPreferences


def _price(part) -> float | None:
    if part.street_price_cents is None:
        return None
    return round(part.street_price_cents / 100, 2)


# ---------------------------------------------------------------------------
# Serializers — one per component type.
# ---------------------------------------------------------------------------

def _serialize_cpu(p: CPU) -> dict:
    return {
        "name": p.name,
        "brand": p.brand,
        "cores": p.cores,
        "threads": p.threads,
        "base_clock_ghz": p.base_clock_ghz,
        "boost_clock_ghz": p.boost_clock_ghz,
        "tdp_w": p.tdp_watts,
        "socket": p.socket,
        "ddr_gen": p.ddr_generation,
        "has_integrated_graphics": p.has_igpu,
        "street_price_usd": _price(p),
    }


def _serialize_cooler(p: CPUCooler) -> dict:
    return {
        "name": p.name,
        "type": p.cooler_type,
        "max_tdp_w": p.max_tdp_watts,
        "noise_db": p.noise_dba,
        "height_mm": p.height_mm,
        "street_price_usd": _price(p),
    }


def _serialize_motherboard(p: Motherboard) -> dict:
    return {
        "name": p.name,
        "socket": p.socket,
        "chipset": p.chipset,
        "form_factor": p.form_factor,
        "ddr_gen": p.ddr_generation,
        "ram_slots": p.memory_slots,
        "m2_slots": p.m2_slots,
        "sata_ports": p.sata_ports,
        "has_wifi": p.has_wifi,
        "street_price_usd": _price(p),
    }


def _serialize_ram(p: RAM) -> dict:
    return {
        "name": p.name,
        "ddr_gen": p.ddr_generation,
        "capacity_gb": p.capacity_gb,
        "speed_mhz": p.speed_mhz,
        "kit_count": p.modules,
        "street_price_usd": _price(p),
    }


def _serialize_storage(p: Storage) -> dict:
    return {
        "name": p.name,
        "interface": p.interface,
        "capacity_gb": p.capacity_gb,
        "seq_read_mbs": p.read_speed_mbps,
        "seq_write_mbs": p.write_speed_mbps,
        "street_price_usd": _price(p),
    }


def _serialize_gpu(p: GPU) -> dict:
    return {
        "name": p.name,
        "brand": p.brand,
        "vram_gb": p.vram_gb,
        "tdp_w": p.tdp_watts,
        "length_mm": p.length_mm,
        "pcie_slots": p.width_slots,
        "street_price_usd": _price(p),
        "used_market_viable": p.used_market_viable,
    }


def _serialize_psu(p: PSU) -> dict:
    return {
        "name": p.name,
        "wattage": p.wattage,
        "efficiency": p.efficiency_rating,
        "modular": p.modular,
        "form_factor": p.form_factor,
        "street_price_usd": _price(p),
    }


def _serialize_case(p: Case) -> dict:
    return {
        "name": p.name,
        "size": p.size,
        "supported_mobo_sizes": p.supported_mobo_form_factors,
        "max_gpu_length_mm": p.max_gpu_length_mm,
        "max_cooler_height_mm": p.max_cooler_height_mm,
        "fan_slots": p.max_fan_slots,
        "included_fans": p.included_fan_count,
        "street_price_usd": _price(p),
    }


def _serialize_fan(p: Fan) -> dict:
    return {
        "name": p.name,
        "size_mm": p.size_mm,
        "airflow_cfm": p.airflow_cfm,
        "noise_db": p.noise_dba,
        "pack_count": p.pack_count,
        "street_price_usd": _price(p),
    }


def _to_json(parts: list, serializer) -> str:
    return json.dumps([serializer(p) for p in parts], indent=None)


# ---------------------------------------------------------------------------
# Public query functions
# ---------------------------------------------------------------------------

async def get_cpu_candidates(
    session: AsyncSession,
    budget_ceiling_usd: int,
    preferences: UserPreferences,
) -> str:
    parts = await crud.get_cpu_candidates(session, budget_ceiling_usd, preferences)
    return _to_json(parts, _serialize_cpu)


async def get_cooler_candidates(
    session: AsyncSession,
    cpu_tdp_w: int,
    cpu_socket: str,
    budget_ceiling_usd: int,
    form_factor: str,
) -> str:
    parts = await crud.get_cooler_candidates(session, cpu_tdp_w, cpu_socket, budget_ceiling_usd)
    return _to_json(parts, _serialize_cooler)


async def get_motherboard_candidates(
    session: AsyncSession,
    cpu_socket: str,
    ddr_gens: list[str],
    budget_ceiling_usd: int,
    form_factor: str,
    wifi_required: bool,
) -> str:
    parts = await crud.get_motherboard_candidates(
        session, cpu_socket, ddr_gens, budget_ceiling_usd, form_factor, wifi_required
    )
    return _to_json(parts, _serialize_motherboard)


async def get_ram_candidates(
    session: AsyncSession,
    ddr_gen: str,
    budget_ceiling_usd: int,
) -> str:
    parts = await crud.get_ram_candidates(session, ddr_gen, budget_ceiling_usd)
    return _to_json(parts, _serialize_ram)


async def get_storage_candidates(
    session: AsyncSession,
    budget_ceiling_usd: int,
    mobo_m2_slots: int,
    mobo_sata_ports: int,
) -> str:
    parts = await crud.get_storage_candidates(session, budget_ceiling_usd, mobo_m2_slots, mobo_sata_ports)
    return _to_json(parts, _serialize_storage)


async def get_gpu_candidates(
    session: AsyncSession,
    budget_ceiling_usd: int,
    case_max_gpu_length_mm: int | None,
    preferences: UserPreferences,
) -> str:
    parts = await crud.get_gpu_candidates(session, budget_ceiling_usd, case_max_gpu_length_mm, preferences)
    return _to_json(parts, _serialize_gpu)


async def get_psu_candidates(
    session: AsyncSession,
    min_wattage: int,
    budget_ceiling_usd: int,
    psu_form_factor: str,
) -> str:
    parts = await crud.get_psu_candidates(session, min_wattage, budget_ceiling_usd, psu_form_factor)
    return _to_json(parts, _serialize_psu)


async def get_case_candidates(
    session: AsyncSession,
    budget_ceiling_usd: int,
    mobo_form_factor: str,
    psu_form_factor: str,
) -> str:
    parts = await crud.get_case_candidates(session, budget_ceiling_usd, mobo_form_factor, psu_form_factor)
    return _to_json(parts, _serialize_case)


async def get_fan_candidates(
    session: AsyncSession,
    budget_ceiling_usd: int,
    case_fan_slots: list[int],
) -> str:
    parts = await crud.get_fan_candidates(session, budget_ceiling_usd, case_fan_slots)
    return _to_json(parts, _serialize_fan)


async def get_ddr_candidates(session: AsyncSession, budget_ceiling_usd: int) -> str:
    """
    Summarize DDR4 vs DDR5 platform cost-efficiency within budget.
    Queries actual subclass models so ddr_generation comes from typed columns.
    CPU supports multiple DDR gens (ARRAY), so each gen it supports is counted.
    """
    all_cpus = await crud.get_all_cpus_active(session)
    all_mobos = await crud.get_all_motherboards_active(session)
    all_ram = await crud.get_all_ram_active(session)

    cpu_prices: dict[str, list[float]] = defaultdict(list)
    cpu_counts: dict[str, int] = defaultdict(int)
    mobo_prices: dict[str, list[float]] = defaultdict(list)
    ram_prices: dict[str, list[float]] = defaultdict(list)

    for p in all_cpus:
        price = _price(p)
        if not price or price > budget_ceiling_usd:
            continue
        gens = p.ddr_generation or []
        for gen in gens:
            cpu_prices[gen].append(price)
            cpu_counts[gen] += 1

    for p in all_mobos:
        price = _price(p)
        gen = p.ddr_generation
        if gen and price:
            mobo_prices[gen].append(price)

    for p in all_ram:
        price = _price(p)
        gen = p.ddr_generation
        if gen and price:
            ram_prices[gen].append(price)

    def _avg(lst: list[float]) -> float | None:
        return round(sum(lst) / len(lst), 2) if lst else None

    gens = sorted(set(cpu_prices) | set(mobo_prices) | set(ram_prices))
    rows = [
        {
            "ddr_gen": gen,
            "avg_cpu_price_usd": _avg(cpu_prices[gen]),
            "avg_mobo_price_usd": _avg(mobo_prices[gen]),
            "avg_ram_price_usd": _avg(ram_prices[gen]),
            "cpu_count": cpu_counts[gen],
        }
        for gen in gens
    ]
    return json.dumps(rows, indent=None)
