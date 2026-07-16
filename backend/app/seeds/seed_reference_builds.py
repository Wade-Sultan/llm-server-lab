from app.core.db import SessionLocal
from app.data.refbuilds import BUILDS
from app.models.pcbuild import BuildComponentRole, BuildPart, BuildStatus, PCBuild, REQUIRED_COMPONENT_BY_ROLE
from app.models.pcparts import (
    CPU, CPUCooler, Case, GPU, GPUChipset, Motherboard, PCPart, PSU, PSUGroup,
    RAMGroup, RAMKit, StorageDrive, StorageGroup,
)
from app.models.reference_build import ReferenceBuild, ReferenceBuildPart


def _get_or_create_group(db, model, name: str, defaults: dict):
    """Dedupe a component group by its (spec-derived) name across builds — the
    same chipset / RAM spec / drive spec / PSU spec used in multiple reference
    builds resolves to one group row."""
    existing = db.query(model).filter_by(name=name).first()
    if existing:
        return existing
    obj = model(name=name, **defaults)
    db.add(obj)
    db.flush()
    return obj


_COMPONENT_TO_ROLE: dict[str, BuildComponentRole] = {
    "CPU":        BuildComponentRole.CPU,
    "CPU Cooler": BuildComponentRole.CPU_COOLER,
    "Motherboard": BuildComponentRole.MOTHERBOARD,
    "RAM":        BuildComponentRole.RAM,
    "Storage":    BuildComponentRole.STORAGE,
    "GPU":        BuildComponentRole.GPU,
    "PSU":        BuildComponentRole.PSU,
    "Case":       BuildComponentRole.CASE,
}


def _build_key_to_use_cases(build_key: str) -> list[str]:
    if "localllm" in build_key:
        return ["ai", "gaming"]
    if "creator" in build_key:
        return ["gaming", "video_editing", "3d_rendering", "streaming"]
    return ["gaming"]


def _create_typed_part(db, part: dict) -> PCPart:
    """Create the appropriate PCPart subclass from a part spec dict."""
    component = part["component"]
    common = {"name": part["model"], "manufacturer": part["brand"]}

    if component == "CPU":
        obj = CPU(
            **common,
            brand=part["brand"].lower(),
            socket=part["socket"],
            tdp_watts=part["tdp_watts"],
            has_igpu=part["has_igpu"],
            ddr_generation=part["ddr_generation"],
            cores=part["cores"],
            threads=part["threads"],
            base_clock_ghz=part.get("base_clock_ghz"),
            boost_clock_ghz=part.get("boost_clock_ghz"),
            l3_cache_mb=part.get("l3_cache_mb"),
        )
    elif component == "CPU Cooler":
        obj = CPUCooler(
            **common,
            supported_sockets=part["supported_sockets"],
            cooler_type=part["cooler_type"],
            max_tdp_watts=part.get("max_tdp_watts"),
            height_mm=part.get("height_mm"),
            radiator_size_mm=part.get("radiator_size_mm"),
        )
    elif component == "Motherboard":
        obj = Motherboard(
            **common,
            socket=part["socket"],
            form_factor=part["form_factor"],
            ddr_generation=part["ddr_generation"],
            memory_slots=part["memory_slots"],
            has_wifi=part["has_wifi"],
            chipset=part.get("chipset"),
        )
    elif component == "RAM":
        per_module = part["capacity_gb"] // part["modules"] if part["modules"] else part["capacity_gb"]
        grp = _get_or_create_group(
            db, RAMGroup,
            name=f'{part["ddr_generation"].upper()}-{part["speed_mhz"]} '
                 f'{part["capacity_gb"]}GB ({part["modules"]}x{per_module})',
            defaults={
                "ddr_generation": part["ddr_generation"],
                "speed_mhz": part["speed_mhz"],
                "modules": part["modules"],
                "capacity_gb": part["capacity_gb"],
            },
        )
        obj = RAMKit(**common, ram_group_id=grp.id)
    elif component == "Storage":
        grp = _get_or_create_group(
            db, StorageGroup,
            name=f'{part["capacity_gb"]}GB {part["interface"]} {part["storage_type"]}',
            defaults={
                "storage_type": part["storage_type"],
                "form_factor": part["form_factor"],
                "interface": part["interface"],
                "capacity_gb": part["capacity_gb"],
                "read_speed_mbps": part.get("read_speed_mbps"),
                "write_speed_mbps": part.get("write_speed_mbps"),
            },
        )
        obj = StorageDrive(**common, storage_group_id=grp.id)
    elif component == "GPU":
        chip = _get_or_create_group(
            db, GPUChipset,
            name=part["chipset"],
            defaults={
                "vram_gb": part["vram_gb"],
                "tdp_watts": part["tdp_watts"],
                "recommended_psu_watts": part.get("recommended_psu_watts"),
                "vram_type": part.get("vram_type"),
                "pcie_generation": part.get("pcie_generation"),
            },
        )
        obj = GPU(
            **common,
            gpu_chipset_id=chip.id,
            brand=part["gpu_brand"],
            length_mm=part["length_mm"],
        )
    elif component == "PSU":
        grp = _get_or_create_group(
            db, PSUGroup,
            name=f'{part["wattage"]}W {part["efficiency_rating"]} {part["form_factor"]}',
            defaults={
                "wattage": part["wattage"],
                "form_factor": part["form_factor"],
                "efficiency_rating": part["efficiency_rating"],
            },
        )
        obj = PSU(**common, psu_group_id=grp.id)
    elif component == "Case":
        obj = Case(
            **common,
            size=part["size"],
            supported_mobo_form_factors=part["supported_mobo_form_factors"],
            max_gpu_length_mm=part["max_gpu_length_mm"],
            max_cooler_height_mm=part["max_cooler_height_mm"],
        )
    else:
        raise ValueError(f"Unknown component: {component!r}")

    db.add(obj)
    db.flush()
    return obj


def _get_or_create_part(db, part: dict) -> PCPart:
    existing = db.query(PCPart).filter_by(name=part["model"]).first()
    if existing:
        return existing
    return _create_typed_part(db, part)


def seed():
    db = SessionLocal()
    try:
        for build_key, build in BUILDS.items():
            existing_ref = db.query(ReferenceBuild).filter_by(build_key=build_key).first()
            if existing_ref:
                print(f"Skipping {build_key} (already exists)")
                continue

            # --- Create PCBuild template ---
            pc_build = PCBuild(
                name=build["label"],
                description=build["description"],
                status=BuildStatus.RECOMMENDED,
                use_cases=_build_key_to_use_cases(build_key),
                total_price_cents=build["total_approx"] * 100,
            )
            db.add(pc_build)
            db.flush()

            # --- Create ReferenceBuild linked to PCBuild ---
            ref = ReferenceBuild(
                build_key=build_key,
                label=build["label"],
                description=build["description"],
                total_approx=build["total_approx"] * 100,
                pc_build_id=pc_build.id,
            )
            db.add(ref)
            db.flush()

            # --- Create parts and wire up BuildPart + ReferenceBuildPart ---
            for i, part_spec in enumerate(build["parts"]):
                pc_part = _get_or_create_part(db, part_spec)
                role = _COMPONENT_TO_ROLE[part_spec["component"]]

                db.add(BuildPart(
                    build_id=pc_build.id,
                    part_id=pc_part.id,
                    role=role,
                    required_component=REQUIRED_COMPONENT_BY_ROLE.get(role, False),
                    price_at_build=part_spec["approx_price"] * 100,
                ))
                db.add(ReferenceBuildPart(
                    build_id=ref.id,
                    part_id=pc_part.id,
                    component=part_spec["component"],
                    approx_price=part_spec["approx_price"] * 100,
                    sort_order=i,
                ))

            print(f"Seeded {build_key}")

        db.commit()
        print("Done.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
