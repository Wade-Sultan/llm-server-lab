from app.core.db import SessionLocal
from app.data.refbuilds import BUILDS
from app.models.pcbuild import BuildComponentRole, BuildPart, BuildStatus, PCBuild, REQUIRED_COMPONENT_BY_ROLE
from app.models.pcparts import (
    CPU, CPUCooler, Case, GPU, Motherboard, PCPart, PSU, RAM, Storage,
)
from app.models.reference_build import ReferenceBuild, ReferenceBuildPart


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
        return ["local_llm", "gaming"]
    if "creator" in build_key:
        return ["gaming", "video_editing", "streaming"]
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
        obj = RAM(
            **common,
            ddr_generation=part["ddr_generation"],
            speed_mhz=part["speed_mhz"],
            modules=part["modules"],
            capacity_gb=part["capacity_gb"],
        )
    elif component == "Storage":
        obj = Storage(
            **common,
            storage_type=part["storage_type"],
            form_factor=part["form_factor"],
            interface=part["interface"],
            capacity_gb=part["capacity_gb"],
            read_speed_mbps=part.get("read_speed_mbps"),
            write_speed_mbps=part.get("write_speed_mbps"),
        )
    elif component == "GPU":
        obj = GPU(
            **common,
            chipset=part["chipset"],
            brand=part["gpu_brand"],
            vram_gb=part["vram_gb"],
            tdp_watts=part["tdp_watts"],
            length_mm=part["length_mm"],
            vram_type=part.get("vram_type"),
            pcie_generation=part.get("pcie_generation"),
        )
    elif component == "PSU":
        obj = PSU(
            **common,
            wattage=part["wattage"],
            form_factor=part["form_factor"],
            efficiency_rating=part["efficiency_rating"],
        )
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
                total_approx=build["total_approx"],
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
                    approx_price=part_spec["approx_price"],
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
