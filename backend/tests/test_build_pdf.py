"""Guards the deterministic PDF export (services/build_pdf.py).

Determinism is the contract the feature was specified around — the document is
produced by layout code from a frozen snapshot, never generated — so the tests
pin byte-for-byte stability rather than appearance.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.services.build_pdf import render_build_pdf

_BUILD = {
    "label": "Custom Build",
    "description": "Assembled component-by-component.",
    "total_approx": 234599,
    "parts": [
        {
            "component": "cpu",
            "brand": "AMD",
            "model": "Ryzen 7 9800X3D",
            "approx_price": 47999,
            "quantity": 1,
        },
        {
            "component": "fans",
            "brand": "Arctic",
            "model": "P12 PWM",
            "approx_price": 899,
            "quantity": 3,
        },
        # A part the catalog had no price for must render, not crash or show $0.
        {
            "component": "case",
            "brand": "Fractal",
            "model": "North — Chalk White",
            "approx_price": None,
            "quantity": 1,
        },
    ],
}

_CREATED = datetime(2026, 8, 16, tzinfo=UTC)
_URL = "https://example.test/b/tok123"


def test_same_snapshot_renders_identical_bytes():
    first = render_build_pdf(_BUILD, _URL, _CREATED)
    second = render_build_pdf(_BUILD, _URL, _CREATED)
    assert first == second
    assert first.startswith(b"%PDF")


def test_non_latin1_part_names_do_not_crash_the_export():
    # The em dash above already exercises replacement; this pins it explicitly
    # for characters wholly outside latin-1.
    build = dict(_BUILD)
    build["label"] = "Custom Build 建造"
    assert render_build_pdf(build, _URL, _CREATED).startswith(b"%PDF")


def test_reference_build_parts_without_quantity_render():
    """Reference builds predate the quantity field entirely."""
    build = dict(_BUILD)
    build["parts"] = [
        {"component": "gpu", "brand": "NVIDIA", "model": "RTX 5080", "approx_price": 119999}
    ]
    assert render_build_pdf(build, _URL, _CREATED).startswith(b"%PDF")
