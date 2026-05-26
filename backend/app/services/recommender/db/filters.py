"""
filters.py
==========
Pure functions that return SQLAlchemy WHERE conditions for each component type.
No DB calls happen here — these are passed into queries.py.

Column access uses JSONB paths (PCPart.specs["key"]).  Adjust paths to match
your actual schema once spec columns are finalized.
"""

from __future__ import annotations

from sqlalchemy import and_, or_
from sqlalchemy.dialects.postgresql import JSONB

from app.models.pcparts import PCPart
from app.schemas.chat import UserPreferences


# ---------------------------------------------------------------------------
# CPU
# ---------------------------------------------------------------------------

def cpu_filters(
    budget_ceiling_usd: int,
    preferences: UserPreferences,
) -> list:
    conditions: list = [
        PCPart.part_type == "cpu",
        PCPart.is_active == True,  # noqa: E712
        PCPart.street_price_cents <= budget_ceiling_usd * 100,
    ]
    if preferences.preferred_brand_cpu != "no_preference":
        # TODO: adjust JSON path to match your spec schema
        conditions.append(
            PCPart.specs["brand"].astext == preferences.preferred_brand_cpu
        )
    return conditions


# ---------------------------------------------------------------------------
# CPU Cooler
# ---------------------------------------------------------------------------

def cpu_cooler_filters(
    cpu_tdp_w: int,
    cpu_socket: str,
    budget_ceiling_usd: int,
    form_factor: str,
) -> list:
    conditions: list = [
        PCPart.part_type == "cpucooler",
        PCPart.is_active == True,  # noqa: E712
        PCPart.street_price_cents <= budget_ceiling_usd * 100,
        # Cooler must support at least the CPU's TDP
        PCPart.specs["max_tdp_w"].as_integer() >= cpu_tdp_w,
        # Socket compatibility — stored as a JSON array of supported sockets
        PCPart.specs["socket_support"].contains([cpu_socket]),
    ]
    if form_factor == "itx":
        # ITX builds need low-profile or specifically sized AIOs
        # TODO: add height/radiator constraints once schema is clear
        pass
    return conditions


# ---------------------------------------------------------------------------
# Motherboard
# ---------------------------------------------------------------------------

def motherboard_filters(
    cpu_socket: str,
    ddr_gen: str,
    budget_ceiling_usd: int,
    form_factor: str,
    wifi_required: bool,
) -> list:
    conditions: list = [
        PCPart.part_type == "motherboard",
        PCPart.is_active == True,  # noqa: E712
        PCPart.street_price_cents <= budget_ceiling_usd * 100,
        PCPart.specs["socket"].astext == cpu_socket,
        PCPart.specs["ddr_gen"].astext == ddr_gen,
    ]
    if form_factor != "no_preference":
        conditions.append(PCPart.specs["form_factor"].astext == form_factor)
    if wifi_required:
        conditions.append(PCPart.specs["has_wifi"].as_boolean() == True)  # noqa: E712
    return conditions


# ---------------------------------------------------------------------------
# RAM
# ---------------------------------------------------------------------------

def ram_filters(
    ddr_gen: str,
    budget_ceiling_usd: int,
) -> list:
    return [
        PCPart.part_type == "ram",
        PCPart.is_active == True,  # noqa: E712
        PCPart.street_price_cents <= budget_ceiling_usd * 100,
        PCPart.specs["ddr_gen"].astext == ddr_gen,
    ]


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def storage_filters(
    budget_ceiling_usd: int,
    mobo_m2_slots: int,
    mobo_sata_ports: int,
) -> list:
    # Allow any storage that fits the motherboard's available interfaces
    # The LLM will decide on capacity/gen tradeoffs from the candidate list
    conditions: list = [
        PCPart.part_type == "storage",
        PCPart.is_active == True,  # noqa: E712
        PCPart.street_price_cents <= budget_ceiling_usd * 100,
    ]
    # TODO: add M.2 slot / SATA port availability constraints
    return conditions


# ---------------------------------------------------------------------------
# GPU
# ---------------------------------------------------------------------------

def gpu_filters(
    budget_ceiling_usd: int,
    case_max_gpu_length_mm: int | None,
    preferences: UserPreferences,
) -> list:
    conditions: list = [
        PCPart.part_type == "gpu",
        PCPart.is_active == True,  # noqa: E712
        PCPart.street_price_cents <= budget_ceiling_usd * 100,
    ]
    if case_max_gpu_length_mm:
        conditions.append(
            PCPart.specs["length_mm"].as_integer() <= case_max_gpu_length_mm
        )
    if preferences.preferred_brand_gpu != "no_preference":
        conditions.append(
            PCPart.specs["brand"].astext == preferences.preferred_brand_gpu
        )
    return conditions


# ---------------------------------------------------------------------------
# PSU
# ---------------------------------------------------------------------------

def psu_filters(
    min_wattage: int,
    budget_ceiling_usd: int,
    psu_form_factor: str,
) -> list:
    return [
        PCPart.part_type == "psu",
        PCPart.is_active == True,  # noqa: E712
        PCPart.street_price_cents <= budget_ceiling_usd * 100,
        PCPart.specs["wattage"].as_integer() >= min_wattage,
        PCPart.specs["form_factor"].astext == psu_form_factor,
    ]


# ---------------------------------------------------------------------------
# Case
# ---------------------------------------------------------------------------

def case_filters(
    budget_ceiling_usd: int,
    mobo_form_factor: str,
    psu_form_factor: str,
) -> list:
    conditions: list = [
        PCPart.part_type == "case",
        PCPart.is_active == True,  # noqa: E712
        PCPart.street_price_cents <= budget_ceiling_usd * 100,
        # Case must support the motherboard form factor
        PCPart.specs["supported_mobo_sizes"].contains([mobo_form_factor]),
    ]
    if psu_form_factor == "sfx" or psu_form_factor == "sfx_l":
        conditions.append(PCPart.specs["sfx_psu_support"].as_boolean() == True)  # noqa: E712
    return conditions


# ---------------------------------------------------------------------------
# Fans
# ---------------------------------------------------------------------------

def fan_filters(
    budget_ceiling_usd: int,
    case_fan_slots: list[int],  # e.g. [120, 140] for available slot sizes
) -> list:
    if not case_fan_slots:
        return []
    sizes = list(set(case_fan_slots))
    return [
        PCPart.part_type == "fan",
        PCPart.is_active == True,  # noqa: E712
        PCPart.street_price_cents <= budget_ceiling_usd * 100,
        or_(*[PCPart.specs["size_mm"].as_integer() == s for s in sizes]),
    ]