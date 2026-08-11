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
from app.models.pcparts import CPU, Case, CPUCooler, Fan, Motherboard
from app.schemas.chat import UserPreferences
from app.services.recommender.scoring import score_candidates


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
        # Server-platform spec. Null on consumer parts, which is itself the
        # signal — a server build needs the CPU that *has* these numbers.
        "pcie_lanes": p.pcie_lanes,
        "memory_channels": p.memory_channels,
        "supports_ecc": p.supports_ecc,
        "street_price_usd": _price(p),
        # Consumed by scoring.score_candidates and stripped there — the raw
        # suite scores never reach the prompt, only the weighted perf_score
        # derived from them.
        "benchmark_scores": p.benchmark_scores,
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
        # pcie_x16_slots is what caps a multi-GPU build in practice, and the
        # rest is the board's half of the server-platform story.
        "pcie_x16_slots": p.pcie_x16_slots,
        "memory_channels": p.memory_channels,
        "supports_ecc": p.supports_ecc,
        "has_ipmi": p.has_ipmi,
        "street_price_usd": _price(p),
    }


# --- Group serializers (GPU/RAM/Storage/PSU) ------------------------------
# For these four types the main DSPy step chooses a *group* (deduped intrinsic
# spec); the exact SKU is resolved deterministically afterwards. Each serializer
# takes the group object and emits its intrinsic spec; _aggregate_groups folds
# the affordable exacts into one row per group, adding the cheapest board's price
# as `starting_price_usd` (what the resolver will actually buy).


def _serialize_gpu_chipset(g) -> dict:  # g: GPUChipset
    return {
        "chipset": g.name,
        "vram_gb": g.vram_gb,
        "tdp_w": g.tdp_watts,
        "has_ray_tracing": g.has_ray_tracing,
        "street_price_usd": _price(g),
        # Stripped by scoring.score_candidates — see _serialize_cpu.
        "benchmark_scores": g.benchmark_scores,
    }


def _serialize_ram_group(g) -> dict:  # g: RAMGroup
    return {
        "ram_group": g.name,
        "ddr_gen": g.ddr_generation,
        "capacity_gb": g.capacity_gb,
        "speed_mhz": g.speed_mhz,
        "kit_count": g.modules,
        "cas_latency": g.cas_latency,
        "is_ecc": g.is_ecc,
        "module_type": g.module_type,
        "street_price_usd": _price(g),
    }


def _serialize_storage_group(g) -> dict:  # g: StorageGroup
    return {
        "storage_group": g.name,
        "type": g.storage_type,
        "interface": g.interface,
        "capacity_gb": g.capacity_gb,
        "seq_read_mbs": g.read_speed_mbps,
        "seq_write_mbs": g.write_speed_mbps,
        "street_price_usd": _price(g),
    }


def _serialize_psu_group(g) -> dict:  # g: PSUGroup
    return {
        "psu_group": g.name,
        "wattage": g.wattage,
        "efficiency": g.efficiency_rating,
        "form_factor": g.form_factor,
        "modular": g.modular,
        # Wattage alone doesn't say whether a supply can physically feed four
        # GPUs and a two-EPS workstation board — the connector counts do.
        "pcie_8pin": g.pcie_8pin_connectors,
        "pcie_12pin": g.pcie_12pin_connectors,
        "pcie_16pin": g.pcie_16pin_connectors,
        "eps_connectors": g.eps_connectors,
        "street_price_usd": _price(g),
    }


def _aggregate_groups(
    exacts: list, group_of, serialize_group, extra=None
) -> list[dict]:
    """Collapse affordable exacts into one candidate row per group. Price lives on
    the group now (surfaced by serialize_group as street_price_usd), so this just
    dedupes by group, ORs `used_market_viable` across the group's exacts, and
    surfaces one exact-level field via `extra(row, exact)` (e.g. GPU brand, which
    lives on the exact). Rows are sorted cheapest-first."""
    by_id: dict = {}
    for e in exacts:
        g = group_of(e)
        if g is None:
            continue
        row = by_id.get(g.id)
        if row is None:
            row = serialize_group(g)
            row["used_market_viable"] = bool(e.used_market_viable)
            if extra is not None:
                extra(row, e)
            by_id[g.id] = row
            continue
        row["used_market_viable"] = row["used_market_viable"] or bool(
            e.used_market_viable
        )
    return sorted(
        by_id.values(),
        key=lambda r: (
            r.get("street_price_usd") is None,
            r.get("street_price_usd") or 0,
        ),
    )


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
    use_cases: list[str] | None = None,
    answers: dict | None = None,
) -> str:
    """Affordable CPUs, annotated with a workload-weighted `perf_score`.

    use_cases/answers default to None so every existing caller (and the DDR
    step's own summaries) keeps working; without them scoring falls back to the
    balanced default weights rather than being skipped, so the numbers are
    still there — just not tilted toward a specific workload.
    """
    parts = await crud.get_cpu_candidates(session, budget_ceiling_usd, preferences)
    rows = [_serialize_cpu(p) for p in parts]
    score_candidates(rows, "cpu", use_cases or [], answers)
    return json.dumps(rows, indent=None)


async def get_cooler_candidates(
    session: AsyncSession,
    cpu_tdp_w: int,
    cpu_socket: str,
    budget_ceiling_usd: int,
    # ACCEPTED AND IGNORED — a known gap, not a leftover parameter.
    # crud.get_cooler_candidates has no form-factor filter, so nothing narrows
    # the candidate set by size and an ITX build can be offered a cooler that
    # does not physically fit its case.
    #
    # Guaranteed to be a concrete size ("atx"/"matx"/"itx"), never
    # "no_preference": _step_cooler resolves it through _effective_form_factor
    # before calling in. So whoever implements the filter can use this value
    # directly rather than having to decide what an absent preference means.
    #
    # Closing the gap needs a height/clearance constraint in the CRUD query, and
    # a max cooler height per case (or per form factor) that the schema does not
    # currently carry. Left plumbed rather than removed so the call site does not
    # have to change when it lands.
    form_factor: str,  # noqa: ARG001
) -> str:
    parts = await crud.get_cooler_candidates(
        session, cpu_tdp_w, cpu_socket, budget_ceiling_usd
    )
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
    module_types: list[str] | None = None,
) -> str:
    """One candidate per RAM group (deduped intrinsic spec); the exact kit is
    resolved deterministically after the group is chosen.

    module_types comes from the chosen board and is a hard compatibility
    filter, not a preference — see crud.get_ram_candidates."""
    exacts = await crud.get_ram_candidates(
        session, ddr_gen, budget_ceiling_usd, module_types
    )
    rows = _aggregate_groups(exacts, lambda e: e.group, _serialize_ram_group)
    return json.dumps(rows, indent=None)


async def get_storage_candidates(
    session: AsyncSession,
    budget_ceiling_usd: int,
    mobo_m2_slots: int,
    mobo_sata_ports: int,
) -> str:
    """One candidate per storage group (deduped); exact drive resolved after."""
    exacts = await crud.get_storage_candidates(
        session, budget_ceiling_usd, mobo_m2_slots, mobo_sata_ports
    )
    rows = _aggregate_groups(exacts, lambda e: e.group, _serialize_storage_group)
    return json.dumps(rows, indent=None)


async def get_gpu_chipset_candidates(
    session: AsyncSession,
    budget_ceiling_usd: int,
    preferences: UserPreferences,
    use_cases: list[str] | None = None,
    answers: dict | None = None,
) -> str:
    """One candidate per GPU chipset (group) for the main GPU step, which chooses
    at the chipset level; the exact board is resolved deterministically later
    (once case length + PSU are known). Intrinsic spec (vram, tdp) comes from the
    chipset; `brand` from a representative board (it lives on the exact); and
    starting_price_usd is the cheapest board (what the resolver will buy)."""
    # Length can't be constrained yet (the case isn't chosen), so pass None.
    exacts = await crud.get_gpu_candidates(
        session, budget_ceiling_usd, None, preferences
    )
    rows = _aggregate_groups(
        exacts,
        lambda e: e.chipset,
        _serialize_gpu_chipset,
        extra=lambda row, e: row.setdefault("brand", e.brand),
    )
    score_candidates(rows, "gpu", use_cases or [], answers)
    return json.dumps(rows, indent=None)


async def get_psu_candidates(
    session: AsyncSession,
    min_wattage: int,
    budget_ceiling_usd: int,
    psu_form_factor: str,
) -> str:
    """One candidate per PSU group (deduped); exact unit resolved after."""
    exacts = await crud.get_psu_candidates(
        session, min_wattage, budget_ceiling_usd, psu_form_factor
    )
    rows = _aggregate_groups(exacts, lambda e: e.group, _serialize_psu_group)
    return json.dumps(rows, indent=None)


async def get_case_candidates(
    session: AsyncSession,
    budget_ceiling_usd: int,
    mobo_form_factor: str,
    psu_form_factor: str,
) -> str:
    parts = await crud.get_case_candidates(
        session, budget_ceiling_usd, mobo_form_factor, psu_form_factor
    )
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
        # ddr generation now lives on the RAM group (eager-loaded); price on the kit.
        gen = p.group.ddr_generation if p.group else None
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
