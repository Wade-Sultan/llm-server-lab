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
