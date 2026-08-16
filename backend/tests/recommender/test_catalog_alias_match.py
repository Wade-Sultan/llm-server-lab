"""
Unit tests for the exact name/alias tier of app.services.recommender.catalog_match.

The normalization is the part worth pinning down. Aliases are typed by hand in
the admin panel and by users in chat, and neither side will agree on case,
spacing or punctuation — "Rainbow 6", "rainbow6" and "RAINBOW-6" have to be one
key, or curating aliases becomes an exercise in guessing how a user will type.

The vector-search tier is not exercised here: it needs a live embedding API and
a populated pgvector table, and scripts/probe_catalog_match.py is the tool for
measuring it against real data.
"""

from __future__ import annotations

from app.services.recommender.catalog_match import (
    CatalogRequirements,
    _normalize_term,
)


def test_normalization_folds_case():
    assert _normalize_term("RAINBOW SIX") == _normalize_term("rainbow six")


def test_normalization_folds_spacing_and_punctuation():
    """The four ways a person writes the same name must be one key."""
    variants = ["Rainbow 6", "rainbow6", "RAINBOW-6", "rainbow_6"]
    normalized = {_normalize_term(v) for v in variants}
    assert len(normalized) == 1


def test_normalization_folds_surrounding_whitespace():
    assert _normalize_term("  R6  ") == _normalize_term("R6")


def test_normalization_folds_colons():
    """Sequel and subtitle punctuation is exactly where users diverge."""
    assert _normalize_term("Rainbow Six: Siege") == _normalize_term("Rainbow Six Siege")


def test_normalization_keeps_distinct_names_distinct():
    """Folding must not go so far that different titles collide."""
    assert _normalize_term("Valorant") != _normalize_term("Valheim")
    assert _normalize_term("R6") != _normalize_term("R7")


def test_empty_and_whitespace_terms_normalize_to_empty():
    """_match_by_alias short-circuits on these rather than matching a row whose
    alias list happens to contain an empty string."""
    assert _normalize_term("") == ""
    assert _normalize_term("   ") == ""


# --- What the summary tells the build steps -----------------------------------


def test_an_unmatched_model_is_reported_rather_than_dropped():
    """THE REGRESSION. A model absent from the catalog used to vanish entirely:
    is_empty was true, summary() returned "", and the GPU step was never told the
    user had named anything. It then sized a $15000 96GB card for a 31B model
    that fits a 24GB card at q4."""
    req = CatalogRequirements(unmatched_terms=["Gemma 4 31B"])

    assert not req.is_empty
    summary = req.summary()
    assert "Gemma 4 31B" in summary
    assert "NOT in our catalog" in summary


def test_an_unmatched_model_carries_the_quantization_arithmetic():
    """Naming the gap is not enough — the step needs the math to close it."""
    summary = CatalogRequirements(unmatched_terms=["Gemma 4 31B"]).summary()

    assert "bytes_per_weight" in summary
    assert "q4 = 0.5" in summary


def test_a_fully_empty_result_still_says_nothing():
    """No terms at all must stay silent; the primer is for named-but-unknown
    models, not for every build that mentioned no software."""
    assert CatalogRequirements().summary() == ""
    assert CatalogRequirements().is_empty


def test_matched_and_unmatched_terms_coexist_in_one_summary():
    req = CatalogRequirements(
        matched_names=["Llama 3.1 70B"],
        unmatched_terms=["Gemma 4 31B"],
        notes=["Llama 3.1 70B (inference at q4): needs 42GB VRAM"],
        min_vram_gb=42,
    )
    summary = req.summary()

    assert "Llama 3.1 70B" in summary
    assert "Gemma 4 31B" in summary
    assert "at least 42GB of VRAM" in summary
