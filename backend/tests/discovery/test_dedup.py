"""
Unit tests for the deterministic dedup matcher of the parts-discovery
pipeline (app.services.discovery.dedup). Pure-logic: candidates are
hand-built, no DB.
"""

from __future__ import annotations

import uuid

from app.services.discovery.dedup import CatalogCandidate, match_item


def _cand(name: str, model_number: str | None = None) -> CatalogCandidate:
    return CatalogCandidate(id=uuid.uuid4(), name=name, model_number=model_number)


def test_exact_match_after_normalization():
    c = _cand("AMD Ryzen 7 9800X3D")
    matched, method, score = match_item("amd ryzen 7 9800x3d", None, [c])
    assert matched == c.id
    assert method == "exact"
    assert score == 1.0


def test_exact_match_ignores_hyphens_and_spacing():
    c = _cand("Intel Core Ultra 9 285K")
    matched, method, _ = match_item("Intel Core-Ultra-9 285K", None, [c])
    assert matched == c.id
    assert method == "exact"


def test_model_number_tier():
    c = _cand("Ryzen 7 9800X3D Desktop Processor", model_number="100-100001084WOF")
    matched, method, score = match_item(
        "AMD Ryzen 7 9800X3D (8-core)", "100-100001084wof", [c]
    )
    assert matched == c.id
    assert method == "model_number"
    assert score == 1.0


def test_fuzzy_tier_close_name():
    c = _cand("GeForce RTX 5080 SUPER 16GB")
    matched, method, score = match_item("RTX 5080 Super", None, [c])
    assert matched == c.id
    assert method == "fuzzy"
    assert score is not None and 0.9 <= score <= 1.0


def test_no_match_returns_nones():
    matched, method, score = match_item(
        "AMD Ryzen 9 9950X3D", None, [_cand("Intel Core i3-12100F")]
    )
    assert matched is None
    assert method is None
    assert score is None


def test_empty_candidates():
    assert match_item("anything", "mn-1", []) == (None, None, None)


def test_tier_precedence_exact_beats_model_number():
    exact = _cand("Ryzen 5 9600X", model_number="A")
    by_mn = _cand("Some Other CPU", model_number="mn-42")
    matched, method, _ = match_item("ryzen 5 9600x", "mn-42", [by_mn, exact])
    assert matched == exact.id
    assert method == "exact"
