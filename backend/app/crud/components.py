"""
CRUD operations for individual PC component types.

Each function queries the actual subclass model (CPU, GPU, etc.) so filters
hit typed columns rather than Python-only @property values.
"""

from __future__ import annotations

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


def _normalize(value: str) -> str:
    """Fold a free-text compatibility field (socket, ddr_generation, ...) to a
    comparable form."""
    return value.strip().lower()


# ---------------------------------------------------------------------------
# CPU
# ---------------------------------------------------------------------------

async def get_cpu_by_name(db: AsyncSession, name: str) -> CPU | None:
    stmt = select(CPU).where(CPU.name == name, CPU.is_active == True)  # noqa: E712
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
    ddr_gen: str,
    budget_ceiling_usd: int,
    form_factor: str,
    wifi_required: bool,
) -> list[Motherboard]:
    stmt = select(Motherboard).where(
        Motherboard.is_active == True,  # noqa: E712
        Motherboard.street_price_cents <= budget_ceiling_usd * 100,
        # socket/ddr_generation are free-text admin-entered fields, not an
        # enum — normalize both sides so e.g. "AM5"/"am5" or "DDR5"/"ddr5"
        # still match instead of silently returning zero candidates.
        func.lower(func.trim(Motherboard.socket)) == _normalize(cpu_socket),
        func.lower(func.trim(Motherboard.ddr_generation)) == _normalize(ddr_gen),
    )
    if form_factor != "no_preference":
        # form_factor is a fixed lowercase Literal from preferences, but
        # Motherboard.form_factor is the same free-text admin field as
        # socket/ddr_generation above — normalize it too.
        stmt = stmt.where(func.lower(func.trim(Motherboard.form_factor)) == _normalize(form_factor))
    if wifi_required:
        stmt = stmt.where(Motherboard.has_wifi == True)  # noqa: E712
    result = await db.execute(stmt)
    return list(result.scalars().all())


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
