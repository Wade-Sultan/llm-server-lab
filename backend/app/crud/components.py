"""
CRUD operations for individual PC component types.

Each function queries the actual subclass model (CPU, GPU, etc.) so filters
hit typed columns rather than Python-only @property values.
"""

from __future__ import annotations

import re

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pcparts import CPU, GPU, Case, CPUCooler, Fan, Motherboard, PCPart, PSU, RAM, Storage
from app.schemas.chat import UserPreferences


# ---------------------------------------------------------------------------
# Generic (polymorphic base)
# ---------------------------------------------------------------------------

async def get_part_by_name(db: AsyncSession, name: str) -> PCPart | None:
    """Case-insensitive lookup on the polymorphic base — any part type."""
    stmt = select(PCPart).where(
        func.lower(PCPart.name) == name.lower(),
        PCPart.is_active == True,  # noqa: E712
    ).limit(1)
    result = await db.execute(stmt)
    return result.scalars().first()


# Drop a leading "Socket " qualifier some sources prepend ("Socket AM5" → "am5").
_SOCKET_PREFIX_RE = re.compile(r"^socket\s*")
# Collapse away internal whitespace/hyphens ("LGA 1700"/"LGA-1700" → "lga1700").
_INNER_SEP_RE = re.compile(r"[\s\-]+")


def _normalize(value: str) -> str:
    """Fold a free-text compatibility field (socket, ddr_generation, form
    factor, ...) to a comparable form so independently admin-entered variants
    still match instead of silently returning zero candidates.

    Beyond lowercasing/trimming, this drops a leading "Socket " qualifier and
    removes internal whitespace and hyphens — so "Socket AM5"/"AM5",
    "LGA 1700"/"LGA1700", and "LGA-1700"/"LGA1700" all compare equal.
    Underscores are preserved on purpose: some values are keyed on them (e.g.
    the "sfx_l" PSU form factor compared against a lower/trim SQL column)."""
    v = value.strip().lower()
    v = _SOCKET_PREFIX_RE.sub("", v)
    v = _INNER_SEP_RE.sub("", v)
    return v


# ---------------------------------------------------------------------------
# CPU
# ---------------------------------------------------------------------------

async def get_cpu_by_name(db: AsyncSession, name: str) -> CPU | None:
    # The LLM is asked to echo an exact candidate name, but it drifts on casing
    # and whitespace ("AMD Ryzen 5 7600X " vs "AMD Ryzen 5 7600X"). Match
    # case-insensitively and trimmed so a near-miss still resolves the CPU
    # instead of silently leaving socket/tdp/ddr at their defaults.
    stmt = select(CPU).where(
        func.lower(func.trim(CPU.name)) == name.strip().lower(),
        CPU.is_active == True,  # noqa: E712
    ).limit(1)
    result = await db.execute(stmt)
    return result.scalars().first()


async def get_cpu_candidates(
    db: AsyncSession,
    budget_ceiling_usd: int,
    preferences: UserPreferences,
) -> list[CPU]:
    stmt = select(CPU).where(
        CPU.is_active == True,  # noqa: E712
        CPU.street_price_cents <= budget_ceiling_usd * 100,
    )
    if preferences.preferred_brand_cpu != "no_preference":
        stmt = stmt.where(CPU.brand == preferences.preferred_brand_cpu)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_all_cpus_active(db: AsyncSession) -> list[CPU]:
    stmt = select(CPU).where(CPU.is_active == True)  # noqa: E712
    result = await db.execute(stmt)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# GPU
# ---------------------------------------------------------------------------

async def get_gpu_by_name(db: AsyncSession, name: str) -> GPU | None:
    stmt = select(GPU).where(GPU.name == name, GPU.is_active == True)  # noqa: E712
    result = await db.execute(stmt)
    return result.scalars().first()


async def get_gpus_for_chipset(db: AsyncSession, chipset: str) -> list[GPU]:
    """All active board variants of a chipset (e.g. every RTX 5080 board). Used
    by the deterministic post-case step that resolves the exact board. chipset
    is a free-text admin field, so match on the normalized value."""
    stmt = select(GPU).where(GPU.is_active == True)  # noqa: E712
    result = await db.execute(stmt)
    target = _normalize(chipset)
    return [g for g in result.scalars().all() if _normalize(g.chipset or "") == target]


async def get_gpu_candidates(
    db: AsyncSession,
    budget_ceiling_usd: int,
    case_max_gpu_length_mm: int | None,
    preferences: UserPreferences,
) -> list[GPU]:
    stmt = select(GPU).where(
        GPU.is_active == True,  # noqa: E712
        GPU.street_price_cents <= budget_ceiling_usd * 100,
    )
    if case_max_gpu_length_mm:
        stmt = stmt.where(GPU.length_mm <= case_max_gpu_length_mm)
    if preferences.preferred_brand_gpu != "no_preference":
        stmt = stmt.where(GPU.brand == preferences.preferred_brand_gpu)
    result = await db.execute(stmt)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# CPU Cooler
# ---------------------------------------------------------------------------

async def get_cooler_by_name(db: AsyncSession, name: str) -> CPUCooler | None:
    stmt = select(CPUCooler).where(CPUCooler.name == name, CPUCooler.is_active == True)  # noqa: E712
    result = await db.execute(stmt)
    return result.scalars().first()


async def get_cooler_candidates(
    db: AsyncSession,
    cpu_tdp_w: int,
    cpu_socket: str,
    budget_ceiling_usd: int,
) -> list[CPUCooler]:
    # supported_sockets is a free-text, admin-entered array (e.g. "LGA1700, AM5"),
    # so array containment can't rely on exact casing/whitespace matching the
    # CPU's own socket string — filter in Python against normalized values
    # instead of CPUCooler.supported_sockets.contains([cpu_socket]).
    stmt = select(CPUCooler).where(
        CPUCooler.is_active == True,  # noqa: E712
        CPUCooler.street_price_cents <= budget_ceiling_usd * 100,
        CPUCooler.max_tdp_watts >= cpu_tdp_w,
    )
    result = await db.execute(stmt)
    target = _normalize(cpu_socket)
    return [
        c for c in result.scalars().all()
        if target in {_normalize(s) for s in (c.supported_sockets or [])}
    ]


# ---------------------------------------------------------------------------
# Motherboard
# ---------------------------------------------------------------------------

async def get_motherboard_by_name(db: AsyncSession, name: str) -> Motherboard | None:
    stmt = select(Motherboard).where(Motherboard.name == name, Motherboard.is_active == True)  # noqa: E712
    result = await db.execute(stmt)
    return result.scalars().first()


async def get_motherboard_candidates(
    db: AsyncSession,
    cpu_socket: str,
    ddr_gens: list[str],
    budget_ceiling_usd: int,
    form_factor: str,
    wifi_required: bool,
) -> list[Motherboard]:
    # socket, ddr_generation and form_factor are all free-text, admin-entered
    # fields (see the admin motherboard form) carrying the same casing/whitespace
    # risk as CPUCooler.supported_sockets — so normalize and match in Python
    # rather than via SQL equality, which only lower/trims.
    #
    # ddr_gens is the CPU's full set of supported DDR generations: a board is
    # compatible if its single ddr_generation is *any* one of them (an Intel CPU
    # that supports both DDR4 and DDR5 can pair with either kind of board).
    stmt = select(Motherboard).where(
        Motherboard.is_active == True,  # noqa: E712
        Motherboard.street_price_cents <= budget_ceiling_usd * 100,
    )
    if wifi_required:
        stmt = stmt.where(Motherboard.has_wifi == True)  # noqa: E712
    result = await db.execute(stmt)

    target_socket = _normalize(cpu_socket)
    target_gens = {_normalize(g) for g in ddr_gens if g and g.strip()}
    target_ff = _normalize(form_factor) if form_factor != "no_preference" else None

    boards: list[Motherboard] = []
    for b in result.scalars().all():
        if _normalize(b.socket or "") != target_socket:
            continue
        if _normalize(b.ddr_generation or "") not in target_gens:
            continue
        if target_ff is not None and _normalize(b.form_factor or "") != target_ff:
            continue
        boards.append(b)
    return boards


async def get_all_motherboards_active(db: AsyncSession) -> list[Motherboard]:
    stmt = select(Motherboard).where(Motherboard.is_active == True)  # noqa: E712
    result = await db.execute(stmt)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# RAM
# ---------------------------------------------------------------------------

async def get_ram_candidates(
    db: AsyncSession,
    ddr_gen: str,
    budget_ceiling_usd: int,
) -> list[RAM]:
    stmt = select(RAM).where(
        RAM.is_active == True,  # noqa: E712
        RAM.street_price_cents <= budget_ceiling_usd * 100,
        func.lower(func.trim(RAM.ddr_generation)) == _normalize(ddr_gen),
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_all_ram_active(db: AsyncSession) -> list[RAM]:
    stmt = select(RAM).where(RAM.is_active == True)  # noqa: E712
    result = await db.execute(stmt)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

async def get_storage_candidates(
    db: AsyncSession,
    budget_ceiling_usd: int,
    mobo_m2_slots: int,
    mobo_sata_ports: int,
) -> list[Storage]:
    if mobo_m2_slots == 0 and mobo_sata_ports == 0:
        return []

    interface_conditions = []
    if mobo_m2_slots > 0:
        interface_conditions.append(Storage.interface.like("pcie%"))
    if mobo_sata_ports > 0:
        interface_conditions.append(Storage.interface == "sata3")

    stmt = select(Storage).where(
        Storage.is_active == True,  # noqa: E712
        Storage.street_price_cents <= budget_ceiling_usd * 100,
    )
    if interface_conditions:
        stmt = stmt.where(or_(*interface_conditions))
    result = await db.execute(stmt)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# PSU
# ---------------------------------------------------------------------------

async def get_psu_by_name(db: AsyncSession, name: str) -> PSU | None:
    stmt = select(PSU).where(PSU.name == name, PSU.is_active == True)  # noqa: E712
    result = await db.execute(stmt)
    return result.scalars().first()


async def get_psu_candidates(
    db: AsyncSession,
    min_wattage: int,
    budget_ceiling_usd: int,
    psu_form_factor: str,
) -> list[PSU]:
    stmt = select(PSU).where(
        PSU.is_active == True,  # noqa: E712
        PSU.street_price_cents <= budget_ceiling_usd * 100,
        PSU.wattage >= min_wattage,
        # PSU.form_factor is the same free-text admin field as
        # Motherboard.socket/ddr_generation — normalize it too.
        func.lower(func.trim(PSU.form_factor)) == _normalize(psu_form_factor),
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Case
# ---------------------------------------------------------------------------

async def get_case_by_name(db: AsyncSession, name: str) -> Case | None:
    stmt = select(Case).where(Case.name == name, Case.is_active == True)  # noqa: E712
    result = await db.execute(stmt)
    return result.scalars().first()


async def get_case_candidates(
    db: AsyncSession,
    budget_ceiling_usd: int,
    mobo_form_factor: str,
    psu_form_factor: str,
) -> list[Case]:
    stmt = select(Case).where(
        Case.is_active == True,  # noqa: E712
        Case.street_price_cents <= budget_ceiling_usd * 100,
    )
    # SFX/SFX-L PSU only fits cases that explicitly support it; ATX fits anywhere
    if psu_form_factor in ("sfx", "sfx_l"):
        stmt = stmt.where(Case.max_psu_length_mm.isnot(None))
    result = await db.execute(stmt)
    # supported_mobo_form_factors is a free-text, comma-separated admin field
    # (e.g. "ATX, mATX, ITX") — same casing/whitespace risk as
    # CPUCooler.supported_sockets, so filter in Python against normalized values.
    target = _normalize(mobo_form_factor)
    return [
        c for c in result.scalars().all()
        if target in {_normalize(f) for f in (c.supported_mobo_form_factors or [])}
    ]


# ---------------------------------------------------------------------------
# Fan
# ---------------------------------------------------------------------------

async def get_fan_candidates(
    db: AsyncSession,
    budget_ceiling_usd: int,
    case_fan_slots: list[int],
) -> list[Fan]:
    if not case_fan_slots:
        return []
    sizes = list(set(case_fan_slots))
    stmt = select(Fan).where(
        Fan.is_active == True,  # noqa: E712
        Fan.street_price_cents <= budget_ceiling_usd * 100,
        or_(*[Fan.size_mm == s for s in sizes]),
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())
