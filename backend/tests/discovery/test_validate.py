"""
Unit tests for the deterministic validation stage of the parts-discovery
pipeline (app.services.discovery.validate). Pure-logic: no DB, no LLM.
"""

from __future__ import annotations

from app.services.discovery.validate import validate_item


def _valid_cpu() -> dict:
    return {
        "name": "AMD Ryzen 7 9800X3D",
        "brand": "amd",
        "socket": "AM5",
        "tdp_watts": 120,
        "has_igpu": True,
        "ddr_generation": ["ddr5"],
        "cores": 8,
        "threads": 16,
        "base_clock_ghz": 4.7,
        "boost_clock_ghz": 5.2,
        "msrp_cents": 47900,
    }


def _rules(errors: list[dict] | None) -> set[tuple[str, str]]:
    return {(e["field"], e["rule"]) for e in (errors or [])}


def test_valid_cpu_passes():
    status, errors = validate_item("cpu", _valid_cpu())
    assert status == "passed"
    assert errors is None


def test_tdp_out_of_range_fails():
    fields = _valid_cpu() | {"tdp_watts": 900}
    status, errors = validate_item("cpu", fields)
    assert status == "failed"
    assert ("tdp_watts", "range") in _rules(errors)


def test_missing_required_field_fails():
    fields = _valid_cpu()
    del fields["socket"]
    status, errors = validate_item("cpu", fields)
    assert status == "failed"
    assert ("socket", "required") in _rules(errors)


def test_bad_brand_enum_fails():
    fields = _valid_cpu() | {"brand": "AMD"}  # vocab is lowercase
    status, errors = validate_item("cpu", fields)
    assert status == "failed"
    assert ("brand", "enum") in _rules(errors)


def test_bad_ddr_generation_element_fails():
    fields = _valid_cpu() | {"ddr_generation": ["ddr5", "ddr3"]}
    status, errors = validate_item("cpu", fields)
    assert status == "failed"
    assert ("ddr_generation", "enum") in _rules(errors)


def test_threads_below_cores_fails():
    fields = _valid_cpu() | {"cores": 16, "threads": 8}
    status, errors = validate_item("cpu", fields)
    assert status == "failed"
    assert ("threads", "range") in _rules(errors)


def test_non_numeric_range_field_is_type_error():
    fields = _valid_cpu() | {"cores": "eight"}
    status, errors = validate_item("cpu", fields)
    assert status == "failed"
    assert ("cores", "type") in _rules(errors)


def test_gpu_chipset_minimal_passes():
    status, errors = validate_item(
        "gpu_chipset", {"name": "RTX 5080", "vram_gb": 16, "tdp_watts": 360}
    )
    assert status == "passed"
    assert errors is None


def test_unknown_category_fails():
    status, errors = validate_item("keyboard", {"name": "x"})
    assert status == "failed"
    assert ("category", "enum") in _rules(errors)


def test_multiple_errors_all_reported():
    fields = _valid_cpu() | {"tdp_watts": 5, "brand": "nvidia"}
    status, errors = validate_item("cpu", fields)
    assert status == "failed"
    assert {("tdp_watts", "range"), ("brand", "enum")} <= _rules(errors)


# --- The categories added alongside the server use case -----------------------
# Coverage here is aimed at the two things ranges can't catch: the vocabulary
# a category shares with another table (board sizes, module types), and the
# cross-field checks that catch an extraction reading the right kind of number
# off the wrong row of a spec table.


def _valid_motherboard() -> dict:
    return {
        "name": "ASUS Pro WS TRX50-SAGE WIFI",
        "socket": "sTR5",
        "form_factor": "eatx",
        "ddr_generation": "ddr5",
        "memory_slots": 8,
        "has_wifi": True,
        "memory_channels": 4,
        "memory_module_types": ["rdimm"],
        "supports_ecc": True,
    }


def _valid_ram_kit() -> dict:
    return {
        "name": "Kingston Fury Renegade Pro 128GB DDR5-6000",
        "ddr_generation": "ddr5",
        "speed_mhz": 6000,
        "capacity_gb": 128,
        "modules": 4,
        "module_capacity_gb": 32,
        "is_ecc": True,
        "module_type": "rdimm",
    }


def _valid_case() -> dict:
    return {
        "name": "Fractal Design Meshify 2 XL",
        "supported_mobo_form_factors": ["atx", "eatx", "ssi_eeb"],
        "size": "full_tower",
        "max_gpu_length_mm": 460,
        "max_cooler_height_mm": 185,
    }


def test_server_motherboard_passes():
    status, errors = validate_item("motherboard", _valid_motherboard())
    assert status == "passed", errors


def test_server_ram_kit_passes():
    status, errors = validate_item("ram_kit", _valid_ram_kit())
    assert status == "passed", errors


def test_ssi_eeb_is_valid_vocabulary_for_boards_and_cases():
    """Board and case sizes are matched against each other at build time, so a
    token accepted by one table must be accepted by the other."""
    assert validate_item("motherboard", _valid_motherboard())[0] == "passed"
    assert validate_item("case", _valid_case())[0] == "passed"


def test_ssi_eeb_support_rejected_on_a_mini_tower():
    """The error that would produce an unbuildable server config: a case
    claiming to fit a board its footprint can't take."""
    case = _valid_case() | {"size": "mini_tower"}
    status, errors = validate_item("case", case)
    assert status == "failed"
    assert ("supported_mobo_form_factors", "consistency") in _rules(errors)


def test_kit_capacity_must_equal_modules_times_module_capacity():
    """The classic slip: reading 32GB off a 4x32GB kit's per-module column."""
    kit = _valid_ram_kit() | {"capacity_gb": 32}
    status, errors = validate_item("ram_kit", kit)
    assert status == "failed"
    assert ("capacity_gb", "consistency") in _rules(errors)


def test_registered_memory_cannot_be_non_ecc():
    kit = _valid_ram_kit() | {"is_ecc": False}
    status, errors = validate_item("ram_kit", kit)
    assert status == "failed"
    assert ("is_ecc", "consistency") in _rules(errors)


def test_unbuffered_kit_may_be_non_ecc():
    kit = _valid_ram_kit() | {"module_type": "udimm", "is_ecc": False}
    assert validate_item("ram_kit", kit)[0] == "passed"


def test_board_cannot_have_fewer_slots_than_channels():
    board = _valid_motherboard() | {"memory_slots": 2, "memory_channels": 8}
    status, errors = validate_item("motherboard", board)
    assert status == "failed"
    assert ("memory_slots", "consistency") in _rules(errors)


def test_solid_state_drive_with_an_rpm_is_rejected():
    drive = {
        "name": "Samsung 990 Pro 4TB",
        "storage_type": "nvme",
        "form_factor": "m2_2280",
        "interface": "pcie_gen4",
        "capacity_gb": 4000,
        "rpm": 7200,
    }
    status, errors = validate_item("storage_drive", drive)
    assert status == "failed"
    assert ("rpm", "consistency") in _rules(errors)


def test_air_cooler_with_a_radiator_is_rejected():
    cooler = {
        "name": "Noctua NH-U14S TR5-SP6",
        "supported_sockets": ["sTR5", "SP6"],
        "cooler_type": "air",
        "max_tdp_watts": 350,
        "radiator_size_mm": 360,
    }
    status, errors = validate_item("cpu_cooler", cooler)
    assert status == "failed"
    assert ("radiator_size_mm", "consistency") in _rules(errors)


def test_server_cpu_specs_are_inside_the_plausibility_ranges():
    """The desktop-sized bounds these replaced would have failed a 96-core
    Threadripper outright — exactly the discovery the catalog now wants."""
    cpu = _valid_cpu() | {
        "name": "AMD Ryzen Threadripper PRO 7995WX",
        "cores": 96,
        "threads": 192,
        "tdp_watts": 350,
        "pcie_lanes": 128,
        "memory_channels": 8,
        "supports_ecc": True,
        "msrp_cents": 999_900,
    }
    status, errors = validate_item("cpu", cpu)
    assert status == "passed", errors
