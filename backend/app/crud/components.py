"""
CRUD operations for individual PC component types.

Each function queries the actual subclass model (CPU, GPU, etc.) so filters
hit typed columns rather than Python-only @property values.
"""

from __future__ import annotations

import re

from sqlalchemy import func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, with_polymorphic

from app.models.pcparts import (
    CPU,
    GPU,
    PSU,
    Case,
    CPUCooler,
    Fan,
    GPUChipset,
    Motherboard,
    PCPart,
    PSUGroup,
    RAMGroup,
    RAMKit,
    StorageDrive,
    StorageGroup,
)
from app.schemas.chat import NO_BUDGET_CEILING, UserPreferences


def _within_budget(price_column, budget_ceiling_usd: int):
    """Price predicate for a candidate query, or an always-true one.

    Every candidate query filters on price the same way, and every one of them
    has to drop that filter entirely under the 'custom' budget tier — a ceiling
    the user explicitly declined to set must not silently reappear as a
    candidate-set boundary. Centralised so the sentinel is honoured in one
    place rather than nine.
    """
    if budget_ceiling_usd == NO_BUDGET_CEILING:
        return true()
    return price_column <= budget_ceiling_usd * 100


# ---------------------------------------------------------------------------
# Generic (polymorphic base)
# ---------------------------------------------------------------------------

# Eager-load all subclass columns so a polymorphic lookup exposes group FKs
# (gpu_chipset_id, ram_group_id, …) without triggering an async-unsafe lazy load.
_PART_POLY = with_polymorphic(PCPart, "*")


async def get_part_by_name(db: AsyncSession, name: str) -> PCPart | None:
    """Case-insensitive lookup on the polymorphic base — any part type. Subclass
    columns (incl. the group FKs) are eager-loaded for resolve_part_price_cents."""
    stmt = (
        select(_PART_POLY)
        .where(
            func.lower(_PART_POLY.name) == name.lower(),
            _PART_POLY.is_active == True,  # noqa: E712
        )
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalars().first()


# part_type -> (group FK attr on the exact, group model). Price for these types
# lives on the group, not on the exact's pc_parts row.
_GROUP_PRICE_LOOKUP = {
    "gpu": ("gpu_chipset_id", GPUChipset),
    "psu": ("psu_group_id", PSUGroup),
    "ramkit": ("ram_group_id", RAMGroup),
    "storagedrive": ("storage_group_id", StorageGroup),
}


async def resolve_part_price_cents(db: AsyncSession, part: PCPart) -> int | None:
    """Effective street price for a resolved part: for grouped types (GPU/PSU/
    RAMKit/StorageDrive) it lives on the group; everything else uses pc_parts.
    `part` must have its subclass columns loaded (see get_part_by_name)."""
    entry = _GROUP_PRICE_LOOKUP.get(getattr(part, "part_type", None))
    if entry is None:
        return part.street_price_cents
    fk_attr, model = entry
    gid = getattr(part, fk_attr, None)
    if gid is None:
        return None
    grp = await db.get(model, gid)
    return grp.street_price_cents if grp else None


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


async def _exacts_for_group(
    db: AsyncSession, model, group_rel, group_name: str
) -> list:
    """Active exacts belonging to a group, matched on the group's (normalized)
    name. Shared by the deterministic group→cheapest-exact resolution steps for
    RAM/Storage/PSU (GPU uses get_gpus_for_chipset). Every exact accesses its
    parent via the `.group` relationship (eager-loaded here)."""
    stmt = select(model).where(model.is_active == True).options(selectinload(group_rel))  # noqa: E712
    result = await db.execute(stmt)
    target = _normalize(group_name)
    return [
        e
        for e in result.scalars().all()
        if e.group is not None and _normalize(e.group.name or "") == target
    ]


# ---------------------------------------------------------------------------
# CPU
# ---------------------------------------------------------------------------


async def get_cpu_by_name(db: AsyncSession, name: str) -> CPU | None:
    # The LLM is asked to echo an exact candidate name, but it drifts on casing
    # and whitespace ("AMD Ryzen 5 7600X " vs "AMD Ryzen 5 7600X"). Match
    # case-insensitively and trimmed so a near-miss still resolves the CPU
    # instead of silently leaving socket/tdp/ddr at their defaults.
    stmt = (
        select(CPU)
        .where(
            func.lower(func.trim(CPU.name)) == name.strip().lower(),
            CPU.is_active == True,  # noqa: E712
        )
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalars().first()


async def get_cpu_candidates(
    db: AsyncSession,
    budget_ceiling_usd: int,
    preferences: UserPreferences,
) -> list[CPU]:
    stmt = select(CPU).where(
        CPU.is_active == True,  # noqa: E712
        _within_budget(CPU.street_price_cents, budget_ceiling_usd),
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
    stmt = (
        select(GPU)
        .where(func.lower(GPU.name) == name.lower(), GPU.is_active == True)  # noqa: E712
        .options(selectinload(GPU.chipset))
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalars().first()


async def get_gpus_for_chipset(db: AsyncSession, chipset: str) -> list[GPU]:
    """All active board variants (exacts) of a chipset (e.g. every RTX 5080
    board). Used by the deterministic post-case step that resolves the exact
    board. The chipset name lives on GPUChipset and is free-text/admin-entered,
    so match on the normalized value."""
    stmt = (
        select(GPU)
        .where(GPU.is_active == True)  # noqa: E712
        .options(selectinload(GPU.chipset))
    )
    result = await db.execute(stmt)
    target = _normalize(chipset)
    return [
        g
        for g in result.scalars().all()
        if g.chipset is not None and _normalize(g.chipset.name or "") == target
    ]


async def get_gpu_candidates(
    db: AsyncSession,
    budget_ceiling_usd: int,
    case_max_gpu_length_mm: int | None,
    preferences: UserPreferences,
) -> list[GPU]:
    """Affordable GPU board exacts (chipset eager-loaded). queries.py aggregates
    these into one candidate per chipset for the main GPU step."""
    stmt = (
        select(GPU)
        .join(GPUChipset, GPU.gpu_chipset_id == GPUChipset.id)
        .where(
            GPU.is_active == True,  # noqa: E712
            _within_budget(GPUChipset.street_price_cents, budget_ceiling_usd),
        )
        .options(selectinload(GPU.chipset))
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
        _within_budget(CPUCooler.street_price_cents, budget_ceiling_usd),
        CPUCooler.max_tdp_watts >= cpu_tdp_w,
    )
    result = await db.execute(stmt)
    target = _normalize(cpu_socket)
    return [
        c
        for c in result.scalars().all()
        if target in {_normalize(s) for s in (c.supported_sockets or [])}
    ]


# ---------------------------------------------------------------------------
# Motherboard
# ---------------------------------------------------------------------------


async def get_motherboard_by_name(db: AsyncSession, name: str) -> Motherboard | None:
    # `.is_(True)`, not `== True`: both compile to the same predicate, but the
    # explicit form satisfies ruff's E712 outright and needs no suppression.
    # The suppressions that used to sit here were silently orphaned by a
    # `ruff format` pass that moved the closing paren onto its own line — a
    # per-line suppression only applies to the line it sits on, so reformatting
    # can quietly detach one from the code it was written for.
    stmt = select(Motherboard).where(
        Motherboard.name == name, Motherboard.is_active.is_(True)
    )
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
        _within_budget(Motherboard.street_price_cents, budget_ceiling_usd),
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
    module_types: list[str] | None = None,
) -> list[RAMKit]:
    """Affordable RAM kit exacts (group eager-loaded) whose group DDR generation
    matches. queries.py aggregates these into one candidate per RAM group.

    module_types is the chosen board's accepted DIMM types. Registered and
    unbuffered memory are not interchangeable in either direction — a TRX50 or
    WRX90 board will not POST on UDIMMs, and a consumer board rejects RDIMMs —
    so this is a hard filter, not a preference. None (the board didn't record
    any, which is every consumer board today) means unconstrained, keeping the
    candidate set identical to what it was before the column existed.

    Kits whose group has no module_type recorded pass either way: the column is
    new and the seeded consumer groups predate it, so treating a NULL as a
    mismatch would empty the candidate set for existing builds.
    """
    stmt = (
        select(RAMKit)
        .join(RAMGroup, RAMKit.ram_group_id == RAMGroup.id)
        .where(
            RAMKit.is_active == True,  # noqa: E712
            _within_budget(RAMGroup.street_price_cents, budget_ceiling_usd),
            func.lower(func.trim(RAMGroup.ddr_generation)) == _normalize(ddr_gen),
        )
        .options(selectinload(RAMKit.group))
    )
    if module_types:
        wanted = {_normalize(t) for t in module_types if t and t.strip()}
        stmt = stmt.where(
            or_(
                RAMGroup.module_type.is_(None),
                func.lower(func.trim(RAMGroup.module_type)).in_(wanted),
            )
        )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_ram_kits_for_group(db: AsyncSession, group_name: str) -> list[RAMKit]:
    """Active RAM kit exacts of the chosen group (matched on the group name,
    normalized) — for the deterministic cheapest-exact resolution."""
    return await _exacts_for_group(db, RAMKit, RAMKit.group, group_name)


async def get_all_ram_active(db: AsyncSession) -> list[RAMKit]:
    """RAM kit exacts with group loaded — used by get_ddr_candidates to compare
    per-generation platform cost (price on the exact, ddr gen on the group)."""
    stmt = (
        select(RAMKit)
        .where(RAMKit.is_active.is_(True))
        .options(selectinload(RAMKit.group))
    )  # noqa: E712
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
) -> list[StorageDrive]:
    """Affordable storage drive exacts (group eager-loaded) whose group interface
    fits the motherboard's available slots/ports. queries.py aggregates these
    into one candidate per storage group."""
    if mobo_m2_slots == 0 and mobo_sata_ports == 0:
        return []

    interface_conditions = []
    if mobo_m2_slots > 0:
        interface_conditions.append(StorageGroup.interface.like("pcie%"))
    if mobo_sata_ports > 0:
        interface_conditions.append(StorageGroup.interface == "sata3")

    stmt = (
        select(StorageDrive)
        .join(StorageGroup, StorageDrive.storage_group_id == StorageGroup.id)
        .where(
            StorageDrive.is_active == True,  # noqa: E712
            _within_budget(StorageGroup.street_price_cents, budget_ceiling_usd),
        )
        .options(selectinload(StorageDrive.group))
    )
    if interface_conditions:
        stmt = stmt.where(or_(*interface_conditions))
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_storage_drives_for_group(
    db: AsyncSession, group_name: str
) -> list[StorageDrive]:
    """Active storage drive exacts of the chosen group — for cheapest-exact resolution."""
    return await _exacts_for_group(db, StorageDrive, StorageDrive.group, group_name)


# ---------------------------------------------------------------------------
# PSU
# ---------------------------------------------------------------------------


async def get_psu_by_name(db: AsyncSession, name: str) -> PSU | None:
    stmt = (
        select(PSU)
        .where(func.lower(PSU.name) == name.lower(), PSU.is_active == True)  # noqa: E712
        .options(selectinload(PSU.group))
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalars().first()


async def get_psu_candidates(
    db: AsyncSession,
    min_wattage: int,
    budget_ceiling_usd: int,
    psu_form_factor: str,
) -> list[PSU]:
    """Affordable PSU exacts (group eager-loaded) whose group meets the wattage
    floor and form factor. queries.py aggregates these into one candidate per
    PSU group."""
    stmt = (
        select(PSU)
        .join(PSUGroup, PSU.psu_group_id == PSUGroup.id)
        .where(
            PSU.is_active == True,  # noqa: E712
            _within_budget(PSUGroup.street_price_cents, budget_ceiling_usd),
            PSUGroup.wattage >= min_wattage,
            # form_factor is the same free-text admin field as
            # Motherboard.socket/ddr_generation — normalize it too.
            func.lower(func.trim(PSUGroup.form_factor)) == _normalize(psu_form_factor),
        )
        .options(selectinload(PSU.group))
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_psus_for_group(db: AsyncSession, group_name: str) -> list[PSU]:
    """Active PSU exacts of the chosen group — for cheapest-exact resolution."""
    return await _exacts_for_group(db, PSU, PSU.group, group_name)


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
        _within_budget(Case.street_price_cents, budget_ceiling_usd),
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
        c
        for c in result.scalars().all()
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
        _within_budget(Fan.street_price_cents, budget_ceiling_usd),
        or_(*[Fan.size_mm == s for s in sizes]),
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())
