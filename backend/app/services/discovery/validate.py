from __future__ import annotations

from typing import Any

# Deterministic validation — no LLM, no DB. Failed items are still staged
# (validation_status='failed') so extraction bugs stay visible in the queue.

REQUIRED_FIELDS: dict[str, list[str]] = {
    "cpu": [
        "name",
        "brand",
        "socket",
        "tdp_watts",
        "has_igpu",
        "ddr_generation",
        "cores",
        "threads",
    ],
    "gpu_chipset": ["name", "vram_gb", "tdp_watts"],
    "gpu_variant": ["name", "chipset_name", "brand", "length_mm"],
    # Required exactly where the subtype column is nullable=False — an approval
    # missing one of these could not produce a valid pc_parts row.
    "motherboard": [
        "name",
        "socket",
        "form_factor",
        "ddr_generation",
        "memory_slots",
        "has_wifi",
    ],
    "cpu_cooler": ["name", "supported_sockets", "cooler_type"],
    "ram_kit": ["name", "ddr_generation", "speed_mhz", "capacity_gb", "modules"],
    "storage_drive": [
        "name",
        "storage_type",
        "form_factor",
        "interface",
        "capacity_gb",
    ],
    "psu": ["name", "wattage", "form_factor", "efficiency_rating"],
    "case": [
        "name",
        "supported_mobo_form_factors",
        "max_gpu_length_mm",
        "max_cooler_height_mm",
        "size",
    ],
    "fan": ["name", "size_mm"],
    "ai_model": ["name", "family"],
}

# Board/case sizes share one vocabulary because a case's supported list is
# matched against a board's single value; they must agree token-for-token.
_MOBO_FORM_FACTORS = frozenset({"atx", "matx", "itx", "eatx", "ssi_eeb", "ssi_ceb"})

ENUM_VOCAB: dict[str, dict[str, frozenset[str]]] = {
    "cpu": {
        "brand": frozenset({"amd", "intel"}),
        # Add "ddr6" here to accept DDR6 during discovery (see DDRGeneration).
        "ddr_generation": frozenset({"ddr4", "ddr5"}),
    },
    "gpu_chipset": {
        "vram_type": frozenset(
            {
                "gddr5",
                "gddr5x",
                "gddr6",
                "gddr6x",
                "gddr7",
                "hbm2",
                "hbm2e",
                "hbm3",
                "hbm3e",
            }
        ),
    },
    "gpu_variant": {
        "brand": frozenset({"nvidia", "amd", "intel"}),
    },
    "motherboard": {
        "form_factor": _MOBO_FORM_FACTORS,
        # Add "ddr6" here to accept DDR6 during discovery (see DDRGeneration).
        "ddr_generation": frozenset({"ddr4", "ddr5"}),
        "memory_module_types": frozenset({"udimm", "rdimm", "lrdimm"}),
    },
    "cpu_cooler": {
        "cooler_type": frozenset(
            {"air", "aio_120", "aio_140", "aio_240", "aio_280", "aio_360"}
        ),
    },
    "ram_kit": {
        # Add "ddr6" here to accept DDR6 during discovery (see DDRGeneration).
        "ddr_generation": frozenset({"ddr4", "ddr5"}),
        "module_type": frozenset({"udimm", "rdimm", "lrdimm"}),
    },
    "storage_drive": {
        "storage_type": frozenset({"nvme", "ssd", "hdd"}),
        "interface": frozenset({"pcie_gen3", "pcie_gen4", "pcie_gen5", "sata3"}),
    },
    "psu": {
        "form_factor": frozenset({"atx", "sfx", "sfx_l"}),
        "efficiency_rating": frozenset(
            {
                "80plus",
                "80plus_bronze",
                "80plus_silver",
                "80plus_gold",
                "80plus_platinum",
                "80plus_titanium",
            }
        ),
        "modular": frozenset({"full", "semi", "non"}),
    },
    "case": {
        "supported_mobo_form_factors": _MOBO_FORM_FACTORS,
        "size": frozenset({"full_tower", "mid_tower", "mini_tower", "sff"}),
    },
    "fan": {},
    "ai_model": {
        "family": frozenset(
            {
                "llm",
                "multimodal",
                "image_gen",
                "video_gen",
                "speech",
                "audio_gen",
                "vision",
                "embedding",
                "classical",
                "rl",
            }
        ),
    },
}

# (min, max) inclusive plausibility bounds.
RANGES: dict[str, dict[str, tuple[float, float]]] = {
    "cpu": {
        # Upper bounds sized for server silicon, not desktop: a 500W SP5 EPYC
        # and a 96-core Threadripper are real parts, and a range that rejected
        # them would fail exactly the discoveries this catalog now wants.
        "tdp_watts": (15, 600),
        "cores": (2, 256),
        "threads": (2, 512),
        "pcie_lanes": (16, 160),
        "memory_channels": (1, 16),
        "base_clock_ghz": (1.0, 7.0),
        "boost_clock_ghz": (1.0, 7.0),
        "l3_cache_mb": (4, 1152),
        "pcie_generation": (3, 6),
        "max_memory_gb": (16, 6144),
        "year_released": (2000, 2100),
        # $30k ceiling: top-bin EPYC and Threadripper PRO parts list well past
        # the $10k a desktop-only bound assumed.
        "msrp_cents": (2000, 3_000_000),
    },
    "gpu_chipset": {
        # 200GB covers datacenter parts (H200 = 141GB); the old 96GB ceiling
        # was exactly the consumer/workstation top end.
        "vram_gb": (4, 200),
        "tdp_watts": (30, 800),
        "recommended_psu_watts": (200, 2000),
        "base_clock_mhz": (500, 4000),
        "boost_clock_mhz": (500, 4000),
        "pcie_generation": (3, 6),
    },
    "gpu_variant": {
        "length_mm": (140, 460),
        "width_slots": (1, 5),
        "year_released": (2000, 2100),
        "msrp_cents": (5000, 5_000_000),
    },
    "motherboard": {
        "memory_slots": (1, 32),
        "m2_slots": (0, 12),
        "m2_pcie_gen": (3, 6),
        "max_memory_gb": (16, 12288),
        "sata_ports": (0, 16),
        "pcie_x16_slots": (0, 8),
        "pcie_generation": (3, 6),
        "memory_channels": (1, 16),
        "usb_type_a_count": (0, 24),
        "usb_type_c_count": (0, 12),
        "year_released": (2000, 2100),
        "msrp_cents": (5000, 300_000),
    },
    "cpu_cooler": {
        # 800W: a cooler rated for a 500W SP5 EPYC exists; the headroom keeps a
        # correctly-extracted server cooler from failing validation.
        "max_tdp_watts": (35, 800),
        "height_mm": (20, 200),
        "radiator_size_mm": (120, 480),
        "fan_count": (0, 6),
        "fan_size_mm": (40, 200),
        "noise_dba": (0, 60),
        "year_released": (2000, 2100),
        "msrp_cents": (1000, 100_000),
    },
    "ram_kit": {
        "speed_mhz": (1600, 12000),
        # 2TB total: 8x256GB LRDIMM is a real EPYC configuration.
        "capacity_gb": (4, 2048),
        "modules": (1, 16),
        "module_capacity_gb": (2, 256),
        "cas_latency": (10, 60),
        "voltage": (1.0, 2.0),
        "height_mm": (15, 60),
        "year_released": (2000, 2100),
        "msrp_cents": (1000, 2_000_000),
    },
    "storage_drive": {
        "capacity_gb": (120, 128_000),
        "read_speed_mbps": (50, 30_000),
        "write_speed_mbps": (50, 30_000),
        "endurance_tbw": (10, 100_000),
        "rpm": (4200, 15_000),
        "year_released": (2000, 2100),
        "msrp_cents": (2000, 1_000_000),
    },
    "psu": {
        # 2400W: the ceiling for a 240V-only multi-GPU workstation supply.
        "wattage": (200, 2400),
        "fan_size_mm": (0, 200),
        "pcie_8pin_connectors": (0, 16),
        "pcie_12pin_connectors": (0, 8),
        "pcie_16pin_connectors": (0, 8),
        "eps_connectors": (0, 4),
        "depth_mm": (80, 260),
        "year_released": (2000, 2100),
        "msrp_cents": (2000, 200_000),
    },
    "case": {
        "max_gpu_length_mm": (150, 600),
        "max_cooler_height_mm": (40, 250),
        "max_radiator_front_mm": (0, 480),
        "max_radiator_top_mm": (0, 480),
        "max_psu_length_mm": (100, 350),
        "included_fan_count": (0, 12),
        "chamber_count": (1, 3),
        "drive_bays_35": (0, 24),
        "drive_bays_25": (0, 16),
        "max_fan_slots": (0, 20),
        "weight_kg": (1, 40),
        "length_mm": (150, 800),
        "width_mm": (100, 500),
        "height_mm": (150, 900),
        "usb_front_type_a": (0, 8),
        "usb_front_type_c": (0, 4),
        "year_released": (2000, 2100),
        "msrp_cents": (3000, 200_000),
    },
    "fan": {
        "size_mm": (40, 200),
        "max_rpm": (300, 6000),
        "airflow_cfm": (5, 200),
        "noise_dba": (0, 60),
        "pack_count": (1, 12),
        "year_released": (2000, 2100),
        "msrp_cents": (300, 50_000),
    },
    "ai_model": {
        "params_billions": (0.001, 10_000),
        "context_length": (128, 20_000_000),
    },
}


def _err(field: str, rule: str, detail: str) -> dict[str, str]:
    return {"field": field, "rule": rule, "detail": detail}


def validate_item(
    category: str, fields: dict[str, Any]
) -> tuple[str, list[dict] | None]:
    """Returns ('passed'|'failed', errors). Unknown categories fail outright."""
    if category not in REQUIRED_FIELDS:
        return "failed", [_err("category", "enum", f"unknown category {category!r}")]

    errors: list[dict] = []

    for field in REQUIRED_FIELDS[category]:
        if fields.get(field) in (None, "", []):
            errors.append(_err(field, "required", "missing required field"))

    for field, vocab in ENUM_VOCAB[category].items():
        value = fields.get(field)
        if value is None:
            continue
        candidates = value if isinstance(value, list) else [value]
        for v in candidates:
            if not isinstance(v, str) or v not in vocab:
                errors.append(_err(field, "enum", f"{v!r} not in {sorted(vocab)}"))

    for field, (lo, hi) in RANGES[category].items():
        value = fields.get(field)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int | float):
            errors.append(
                _err(field, "type", f"expected number, got {type(value).__name__}")
            )
            continue
        if not lo <= value <= hi:
            errors.append(_err(field, "range", f"{value} outside [{lo}, {hi}]"))

    errors.extend(_cross_field_errors(category, fields))

    return ("failed", errors) if errors else ("passed", None)


def _cross_field_errors(category: str, fields: dict[str, Any]) -> list[dict]:
    """Consistency checks between fields that are each individually plausible.

    These catch the failure mode ranges can't: an extraction that read the
    right kind of number off the wrong row of a spec table.
    """
    errors: list[dict] = []

    def _int(field: str) -> int | None:
        v = fields.get(field)
        return v if isinstance(v, int) and not isinstance(v, bool) else None

    if category == "cpu":
        cores, threads = _int("cores"), _int("threads")
        if cores is not None and threads is not None and threads < cores:
            errors.append(
                _err("threads", "range", f"threads ({threads}) < cores ({cores})")
            )

    elif category == "ram_kit":
        total, modules = _int("capacity_gb"), _int("modules")
        per_module = _int("module_capacity_gb")
        # The classic extraction slip: reading "32GB" off a 4x32GB kit's
        # per-module column and recording it as the kit total.
        if (
            total is not None
            and modules is not None
            and per_module is not None
            and total != modules * per_module
        ):
            errors.append(
                _err(
                    "capacity_gb",
                    "consistency",
                    f"{total}GB != {modules} x {per_module}GB",
                )
            )
        # Registered memory is ECC by definition; the reverse is not true.
        if fields.get("module_type") in ("rdimm", "lrdimm") and (
            fields.get("is_ecc") is False
        ):
            errors.append(
                _err(
                    "is_ecc",
                    "consistency",
                    f"{fields['module_type']} modules are always ECC",
                )
            )

    elif category == "storage_drive":
        # rpm on a solid-state drive means the extractor pulled a comparison
        # row, and interface/type disagreement means the same.
        if fields.get("storage_type") in ("nvme", "ssd") and _int("rpm"):
            errors.append(_err("rpm", "consistency", "solid-state drives have no rpm"))
        if fields.get("storage_type") == "nvme" and fields.get("interface") == "sata3":
            errors.append(_err("interface", "consistency", "nvme drives are not sata3"))
        if fields.get("storage_type") == "hdd" and str(
            fields.get("interface", "")
        ).startswith("pcie"):
            errors.append(_err("interface", "consistency", "hdd on a pcie interface"))

    elif category == "cpu_cooler":
        # An air cooler has no radiator and a closed-loop cooler has no
        # meaningful tower height; carrying both means two products got merged.
        cooler_type = fields.get("cooler_type")
        if cooler_type == "air" and _int("radiator_size_mm"):
            errors.append(
                _err("radiator_size_mm", "consistency", "air coolers have no radiator")
            )
        if isinstance(cooler_type, str) and cooler_type.startswith("aio"):
            radiator = _int("radiator_size_mm")
            expected = cooler_type.removeprefix("aio_")
            if radiator is not None and expected.isdigit():
                # A 360mm rating on an aio_240 is a mislabelled product page.
                if radiator % int(expected) != 0:
                    errors.append(
                        _err(
                            "radiator_size_mm",
                            "consistency",
                            f"{radiator}mm radiator on {cooler_type}",
                        )
                    )

    elif category == "case":
        # A case that fits a board must clear that board's footprint. Catching
        # this here matters more than usual: an SSI-EEB claim on a mini tower
        # is exactly the error that would produce an unbuildable server config.
        sizes = fields.get("supported_mobo_form_factors")
        size = fields.get("size")
        if (
            isinstance(sizes, list)
            and {"eatx", "ssi_eeb", "ssi_ceb"} & set(sizes)
            and size in ("mini_tower", "sff")
        ):
            errors.append(
                _err(
                    "supported_mobo_form_factors",
                    "consistency",
                    f"{size} case claiming {sorted(set(sizes))} support",
                )
            )

    elif category == "motherboard":
        slots, channels = _int("memory_slots"), _int("memory_channels")
        if slots is not None and channels is not None and slots < channels:
            errors.append(
                _err(
                    "memory_slots",
                    "consistency",
                    f"{slots} slots cannot fill {channels} channels",
                )
            )

    return errors
