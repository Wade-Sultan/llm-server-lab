"""
CRUD operations for individual PC component types.

Each function queries the actual subclass model (CPU, GPU, etc.) so filters
hit typed columns rather than Python-only @property values.
"""

from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.pcparts import CPU, GPU, Case, CPUCooler, Fan, Motherboard, PSU, RAM, Storage
from app.schemas.chat import UserPreferences


# ---------------------------------------------------------------------------
# CPU
# ---------------------------------------------------------------------------

def get_cpu_by_name(db: Session, name: str) -> CPU | None:
    return db.query(CPU).filter(CPU.name == name, CPU.is_active == True).first()  # noqa: E712


def get_cpu_candidates(
    db: Session,
    budget_ceiling_usd: int,
    preferences: UserPreferences,
) -> list[CPU]:
    q = db.query(CPU).filter(
        CPU.is_active == True,  # noqa: E712
        CPU.street_price_cents <= budget_ceiling_usd * 100,
    )
    if preferences.preferred_brand_cpu != "no_preference":
        q = q.filter(CPU.brand == preferences.preferred_brand_cpu)
    return q.all()


def get_all_cpus_active(db: Session) -> list[CPU]:
    return db.query(CPU).filter(CPU.is_active == True).all()  # noqa: E712


# ---------------------------------------------------------------------------
# GPU
# ---------------------------------------------------------------------------

def get_gpu_by_name(db: Session, name: str) -> GPU | None:
    return db.query(GPU).filter(GPU.name == name, GPU.is_active == True).first()  # noqa: E712


def get_gpu_candidates(
    db: Session,
    budget_ceiling_usd: int,
    case_max_gpu_length_mm: int | None,
    preferences: UserPreferences,
) -> list[GPU]:
    q = db.query(GPU).filter(
        GPU.is_active == True,  # noqa: E712
        GPU.street_price_cents <= budget_ceiling_usd * 100,
    )
    if case_max_gpu_length_mm:
        q = q.filter(GPU.length_mm <= case_max_gpu_length_mm)
    if preferences.preferred_brand_gpu != "no_preference":
        q = q.filter(GPU.brand == preferences.preferred_brand_gpu)
    return q.all()


# ---------------------------------------------------------------------------
# CPU Cooler
# ---------------------------------------------------------------------------

def get_cooler_by_name(db: Session, name: str) -> CPUCooler | None:
    return db.query(CPUCooler).filter(CPUCooler.name == name, CPUCooler.is_active == True).first()  # noqa: E712


def get_cooler_candidates(
    db: Session,
    cpu_tdp_w: int,
    cpu_socket: str,
    budget_ceiling_usd: int,
) -> list[CPUCooler]:
    return (
        db.query(CPUCooler)
        .filter(
            CPUCooler.is_active == True,  # noqa: E712
            CPUCooler.street_price_cents <= budget_ceiling_usd * 100,
            CPUCooler.max_tdp_watts >= cpu_tdp_w,
            CPUCooler.supported_sockets.contains([cpu_socket]),
        )
        .all()
    )


# ---------------------------------------------------------------------------
# Motherboard
# ---------------------------------------------------------------------------

def get_motherboard_by_name(db: Session, name: str) -> Motherboard | None:
    return db.query(Motherboard).filter(Motherboard.name == name, Motherboard.is_active == True).first()  # noqa: E712


def get_motherboard_candidates(
    db: Session,
    cpu_socket: str,
    ddr_gen: str,
    budget_ceiling_usd: int,
    form_factor: str,
    wifi_required: bool,
) -> list[Motherboard]:
    q = db.query(Motherboard).filter(
        Motherboard.is_active == True,  # noqa: E712
        Motherboard.street_price_cents <= budget_ceiling_usd * 100,
        Motherboard.socket == cpu_socket,
        Motherboard.ddr_generation == ddr_gen,
    )
    if form_factor != "no_preference":
        q = q.filter(Motherboard.form_factor == form_factor)
    if wifi_required:
        q = q.filter(Motherboard.has_wifi == True)  # noqa: E712
    return q.all()


def get_all_motherboards_active(db: Session) -> list[Motherboard]:
    return db.query(Motherboard).filter(Motherboard.is_active == True).all()  # noqa: E712


# ---------------------------------------------------------------------------
# RAM
# ---------------------------------------------------------------------------

def get_ram_candidates(
    db: Session,
    ddr_gen: str,
    budget_ceiling_usd: int,
) -> list[RAM]:
    return (
        db.query(RAM)
        .filter(
            RAM.is_active == True,  # noqa: E712
            RAM.street_price_cents <= budget_ceiling_usd * 100,
            RAM.ddr_generation == ddr_gen,
        )
        .all()
    )


def get_all_ram_active(db: Session) -> list[RAM]:
    return db.query(RAM).filter(RAM.is_active == True).all()  # noqa: E712


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def get_storage_candidates(
    db: Session,
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

    q = db.query(Storage).filter(
        Storage.is_active == True,  # noqa: E712
        Storage.street_price_cents <= budget_ceiling_usd * 100,
    )
    if interface_conditions:
        q = q.filter(or_(*interface_conditions))
    return q.all()


# ---------------------------------------------------------------------------
# PSU
# ---------------------------------------------------------------------------

def get_psu_by_name(db: Session, name: str) -> PSU | None:
    return db.query(PSU).filter(PSU.name == name, PSU.is_active == True).first()  # noqa: E712


def get_psu_candidates(
    db: Session,
    min_wattage: int,
    budget_ceiling_usd: int,
    psu_form_factor: str,
) -> list[PSU]:
    return (
        db.query(PSU)
        .filter(
            PSU.is_active == True,  # noqa: E712
            PSU.street_price_cents <= budget_ceiling_usd * 100,
            PSU.wattage >= min_wattage,
            PSU.form_factor == psu_form_factor,
        )
        .all()
    )


# ---------------------------------------------------------------------------
# Case
# ---------------------------------------------------------------------------

def get_case_by_name(db: Session, name: str) -> Case | None:
    return db.query(Case).filter(Case.name == name, Case.is_active == True).first()  # noqa: E712


def get_case_candidates(
    db: Session,
    budget_ceiling_usd: int,
    mobo_form_factor: str,
    psu_form_factor: str,
) -> list[Case]:
    q = db.query(Case).filter(
        Case.is_active == True,  # noqa: E712
        Case.street_price_cents <= budget_ceiling_usd * 100,
        Case.supported_mobo_form_factors.contains([mobo_form_factor]),
    )
    # SFX/SFX-L PSU only fits cases that explicitly support it; ATX fits anywhere
    if psu_form_factor in ("sfx", "sfx_l"):
        q = q.filter(Case.max_psu_length_mm.isnot(None))
    return q.all()


# ---------------------------------------------------------------------------
# Fan
# ---------------------------------------------------------------------------

def get_fan_candidates(
    db: Session,
    budget_ceiling_usd: int,
    case_fan_slots: list[int],
) -> list[Fan]:
    if not case_fan_slots:
        return []
    sizes = list(set(case_fan_slots))
    return (
        db.query(Fan)
        .filter(
            Fan.is_active == True,  # noqa: E712
            Fan.street_price_cents <= budget_ceiling_usd * 100,
            or_(*[Fan.size_mm == s for s in sizes]),
        )
        .all()
    )
