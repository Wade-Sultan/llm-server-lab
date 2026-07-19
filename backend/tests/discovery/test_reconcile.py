"""
Unit tests for the multi-source reconciliation stage of the parts-discovery
pipeline (app.services.discovery.reconcile). Pure-logic diff/merge.
"""

from __future__ import annotations

from app.services.discovery.reconcile import reconcile


def _src(url: str, values: dict) -> tuple[str, dict, dict]:
    provenance = {
        f: {"source_url": url, "snippet": f"snippet for {f} from {url}"}
        for f in values
    }
    return url, values, provenance


def test_full_agreement():
    extracted, provenance, confidence, urls = reconcile(
        [
            _src("https://a.example", {"cores": 8, "tdp_watts": 120}),
            _src("https://b.example", {"cores": 8, "tdp_watts": 120}),
        ]
    )
    assert extracted == {"cores": 8, "tdp_watts": 120}
    assert confidence["cores"] == {"agreement": 1.0, "n_sources": 2}
    assert "values" not in confidence["cores"]
    assert urls == ["https://a.example", "https://b.example"]


def test_two_vs_one_picks_modal_and_records_disagreement():
    extracted, provenance, confidence, _ = reconcile(
        [
            _src("https://a.example", {"tdp_watts": 120}),
            _src("https://b.example", {"tdp_watts": 105}),
            _src("https://c.example", {"tdp_watts": 120}),
        ]
    )
    assert extracted["tdp_watts"] == 120
    assert confidence["tdp_watts"]["agreement"] == 2 / 3
    assert confidence["tdp_watts"]["values"] == {
        "https://a.example": 120,
        "https://b.example": 105,
        "https://c.example": 120,
    }
    # provenance follows a source that reported the chosen value
    assert provenance["tdp_watts"]["source_url"] in {"https://a.example", "https://c.example"}


def test_tie_breaks_toward_highest_ranked_source():
    extracted, provenance, confidence, _ = reconcile(
        [
            _src("https://top-ranked.example", {"boost_clock_ghz": 5.2}),
            _src("https://lower-ranked.example", {"boost_clock_ghz": 5.4}),
        ]
    )
    assert extracted["boost_clock_ghz"] == 5.2
    assert provenance["boost_clock_ghz"]["source_url"] == "https://top-ranked.example"
    assert confidence["boost_clock_ghz"]["agreement"] == 0.5


def test_field_reported_by_single_source_is_kept():
    extracted, _, confidence, _ = reconcile(
        [
            _src("https://a.example", {"cores": 8}),
            _src("https://b.example", {"cores": 8, "l3_cache_mb": 96}),
        ]
    )
    assert extracted["l3_cache_mb"] == 96
    assert confidence["l3_cache_mb"] == {"agreement": 1.0, "n_sources": 1}


def test_list_values_compare_by_content():
    extracted, _, confidence, _ = reconcile(
        [
            _src("https://a.example", {"ddr_generation": ["ddr5"]}),
            _src("https://b.example", {"ddr_generation": ["ddr5"]}),
        ]
    )
    assert extracted["ddr_generation"] == ["ddr5"]
    assert confidence["ddr_generation"]["agreement"] == 1.0


def test_single_source_passthrough():
    extracted, provenance, confidence, urls = reconcile(
        [_src("https://only.example", {"name": "RTX 5080", "vram_gb": 16})]
    )
    assert extracted == {"name": "RTX 5080", "vram_gb": 16}
    assert provenance["vram_gb"]["source_url"] == "https://only.example"
    assert urls == ["https://only.example"]
