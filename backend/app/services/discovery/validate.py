from __future__ import annotations

from typing import Any

# Deterministic validation — no LLM, no DB. Failed items are still staged
# (validation_status='failed') so extraction bugs stay visible in the queue.

REQUIRED_FIELDS: dict[str, list[str]] = {
    "cpu": ["name", "brand", "socket", "tdp_watts", "has_igpu", "ddr_generation", "cores", "threads"],
    "gpu_chipset": ["name", "vram_gb", "tdp_watts"],
    "gpu_variant": ["name", "chipset_name", "brand", "length_mm"],
}

ENUM_VOCAB: dict[str, dict[str, frozenset[str]]] = {
    "cpu": {
        "brand": frozenset({"amd", "intel"}),
        "ddr_generation": frozenset({"ddr4", "ddr5"}),
    },
    "gpu_chipset": {
        "vram_type": frozenset(
            {"gddr5", "gddr5x", "gddr6", "gddr6x", "gddr7", "hbm2", "hbm2e", "hbm3", "hbm3e"}
        ),
    },
    "gpu_variant": {
        "brand": frozenset({"nvidia", "amd", "intel"}),
    },
}

# (min, max) inclusive plausibility bounds.
RANGES: dict[str, dict[str, tuple[float, float]]] = {
    "cpu": {
        "tdp_watts": (15, 350),
        "cores": (2, 192),
        "threads": (2, 384),
        "base_clock_ghz": (1.0, 7.0),
        "boost_clock_ghz": (1.0, 7.0),
        "l3_cache_mb": (4, 1152),
        "pcie_generation": (3, 6),
        "max_memory_gb": (16, 6144),
        "year_released": (2000, 2100),
        "msrp_cents": (2000, 1_000_000),
    },
    "gpu_chipset": {
        "vram_gb": (4, 96),
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
}


def _err(field: str, rule: str, detail: str) -> dict[str, str]:
    return {"field": field, "rule": rule, "detail": detail}


def validate_item(category: str, fields: dict[str, Any]) -> tuple[str, list[dict] | None]:
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
                errors.append(
                    _err(field, "enum", f"{v!r} not in {sorted(vocab)}")
                )

    for field, (lo, hi) in RANGES[category].items():
        value = fields.get(field)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int | float):
            errors.append(_err(field, "type", f"expected number, got {type(value).__name__}"))
            continue
        if not lo <= value <= hi:
            errors.append(_err(field, "range", f"{value} outside [{lo}, {hi}]"))

    if category == "cpu":
        cores, threads = fields.get("cores"), fields.get("threads")
        if (
            isinstance(cores, int)
            and isinstance(threads, int)
            and threads < cores
        ):
            errors.append(_err("threads", "range", f"threads ({threads}) < cores ({cores})"))

    return ("failed", errors) if errors else ("passed", None)
