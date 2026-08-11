"""
Unit tests for app.services.recommender.scoring.

Pure-logic tests — no DB, no network, no LLM. Two things carry most of the
weight here:

  * The partial-coverage rules. Benchmark data in the catalog is incomplete and
    always will be, so the interesting cases are all about what happens when a
    candidate is missing the axis a workload is decided on.
  * The dominance gate's refusals. A gate that fires when it shouldn't silently
    removes the LLM from a decision that had real judgment in it, and nothing
    downstream would flag that — so each reason it must decline gets its own
    test.
"""

from __future__ import annotations

import pytest

from app.services.recommender import scoring


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cpu(name, price, *, single=None, multi=None):
    scores = {}
    if single is not None:
        scores["cinebench_r24_single"] = single
    if multi is not None:
        scores["cinebench_r24_multi"] = multi
    return {
        "name": name,
        "street_price_usd": price,
        "benchmark_scores": scores or None,
    }


def _gpu(name, price, *, timespy=None, compute=None, speedway=None, port_royal=None):
    scores = {}
    if timespy is not None:
        scores["timespy"] = timespy
    if compute is not None:
        scores["geekbench_6_compute"] = compute
    if speedway is not None:
        scores["speed_way"] = speedway
    if port_royal is not None:
        scores["port_royal"] = port_royal
    return {
        "chipset": name,
        "street_price_usd": price,
        "benchmark_scores": scores or None,
    }


# ---------------------------------------------------------------------------
# Weights
# ---------------------------------------------------------------------------


def test_gaming_weights_favor_single_thread_for_cpu():
    weights = scoring.weights_for("cpu", ["gaming"])
    assert weights["single"] > weights["multi"]


def test_rendering_weights_favor_multi_thread_for_cpu():
    weights = scoring.weights_for("cpu", ["rendering"])
    assert weights["multi"] > weights["single"]


def test_multiple_use_cases_blend_and_renormalize():
    weights = scoring.weights_for("cpu", ["gaming", "rendering"])
    # Blended, so it sits between the two extremes rather than adopting either.
    assert 0.15 < weights["single"] < 0.75
    assert pytest.approx(sum(weights.values()), abs=1e-9) == 1.0


def test_unknown_use_case_falls_back_to_defaults():
    weights = scoring.weights_for("cpu", ["not_a_real_use_case"])
    assert pytest.approx(sum(weights.values()), abs=1e-9) == 1.0
    assert set(weights) == {"single", "multi"}


def test_4k_shifts_cpu_weight_toward_multi_thread():
    """At 4K the GPU pins the frame rate, so single-thread lead matters less."""
    base = scoring.weights_for("cpu", ["gaming"], {})
    at_4k = scoring.weights_for("cpu", ["gaming"], {"gaming.resolution": "4k"})
    at_1080p = scoring.weights_for("cpu", ["gaming"], {"gaming.resolution": "1080p"})
    assert at_4k["single"] < base["single"] < at_1080p["single"]


def test_resolution_does_not_shift_gpu_weights():
    base = scoring.weights_for("gpu", ["gaming"], {})
    at_4k = scoring.weights_for("gpu", ["gaming"], {"gaming.resolution": "4k"})
    assert base == at_4k


# ---------------------------------------------------------------------------
# score_candidates
# ---------------------------------------------------------------------------


def test_best_candidate_scores_one_and_scores_are_relative():
    rows = [
        _cpu("Fast", 400, single=140, multi=1400),
        _cpu("Slow", 200, single=70, multi=700),
    ]
    scoring.score_candidates(rows, "cpu", ["gaming"])
    assert rows[0]["perf_score"] == 1.0
    assert rows[1]["perf_score"] == pytest.approx(0.5, abs=1e-6)


def test_benchmark_scores_are_stripped_from_the_prompt_payload():
    """Raw suite numbers are an input to scoring, never shown to the model."""
    rows = [_cpu("A", 300, single=100, multi=1000)]
    scoring.score_candidates(rows, "cpu", ["gaming"])
    assert "benchmark_scores" not in rows[0]


def test_unscorable_candidate_is_kept_with_a_null_score():
    """Scoring never removes a candidate — the LLM may still choose it."""
    rows = [
        _cpu("Measured", 300, single=100, multi=1000),
        _cpu("Unmeasured", 250),
    ]
    scoring.score_candidates(rows, "cpu", ["gaming"])
    assert len(rows) == 2
    assert rows[1]["perf_score"] is None


def test_missing_dominant_axis_is_unscorable():
    """A rendering pick is 85% multi-thread; single-thread data alone can't rank it.

    Without this rule, renormalizing over available axes would score a part
    that has only single-thread data as a perfect 1.00 on a workload it was
    never measured for.
    """
    rows = [
        _cpu("SingleOnly", 300, single=100),
        _cpu("Both", 320, single=90, multi=1000),
    ]
    scoring.score_candidates(rows, "cpu", ["rendering"])
    assert rows[0]["perf_score"] is None
    assert rows[1]["perf_score"] is not None


def test_missing_minor_axis_is_still_scorable():
    """A 0.10-weight tiebreak axis being absent must not disqualify a candidate."""
    rows = [
        _gpu("NoRayData", 700, timespy=20000, speedway=5000),
        _gpu("Full", 900, timespy=24000, speedway=6000, port_royal=15000),
    ]
    scoring.score_candidates(rows, "gpu", ["gaming"])
    assert rows[0]["perf_score"] is not None


def test_zero_and_negative_benchmarks_are_treated_as_absent():
    """Placeholder zeros must not read as a real measurement of 'very slow'."""
    rows = [
        _cpu("Placeholder", 300, single=0, multi=0),
        _cpu("Real", 320, single=100, multi=1000),
    ]
    scoring.score_candidates(rows, "cpu", ["gaming"])
    assert rows[0]["perf_score"] is None


def test_perf_per_dollar_is_computed_and_omitted_without_a_price():
    rows = [
        _cpu("Priced", 200, single=100, multi=1000),
        _cpu("Unpriced", None, single=100, multi=1000),
    ]
    scoring.score_candidates(rows, "cpu", ["gaming"])
    assert rows[0]["perf_per_dollar"] == pytest.approx(1.0 / 200, abs=1e-6)
    assert "perf_per_dollar" not in rows[1]


def test_two_suites_on_one_axis_are_averaged_not_double_counted():
    """A part measured on both Cinebench and Geekbench must not out-rank one
    measured on a single suite purely for having more rows."""
    both = {
        "name": "Both",
        "street_price_usd": 300,
        "benchmark_scores": {
            "cinebench_r24_single": 100,
            "geekbench_6_single": 3000,
            "cinebench_r24_multi": 1000,
            "geekbench_6_multi": 18000,
        },
    }
    one = _cpu("One", 300, single=100, multi=1000)
    rows = [both, one]
    scoring.score_candidates(rows, "cpu", ["gaming"])
    assert rows[0]["perf_score"] == rows[1]["perf_score"] == 1.0


# ---------------------------------------------------------------------------
# Dominance gate
# ---------------------------------------------------------------------------


def test_dominant_candidate_is_cheapest_and_fastest():
    rows = [
        _cpu("Best", 200, single=140, multi=1400),
        _cpu("Worse", 300, single=100, multi=1000),
    ]
    scoring.score_candidates(rows, "cpu", ["gaming"])
    dominant = scoring.find_dominant(rows, "name")
    assert dominant is not None
    assert dominant.name == "Best"
    # 1.000 / 0.714 - 1. The tolerance is loose because perf_score is rounded
    # to 3 decimals before the margin is taken (see score_candidates), so the
    # margin carries that rounding rather than the exact 1400/1000 ratio.
    assert dominant.margin == pytest.approx(0.4, abs=1e-3)
    # A skipped step still has to populate the same state a run one would.
    assert "Best" in dominant.reason
    assert dominant.reconsideration_threshold


def test_no_dominance_when_the_fastest_costs_more():
    """The common case: more performance for more money is a real tradeoff."""
    rows = [
        _cpu("Fast", 500, single=140, multi=1400),
        _cpu("Cheap", 200, single=100, multi=1000),
    ]
    scoring.score_candidates(rows, "cpu", ["gaming"])
    assert scoring.find_dominant(rows, "name") is None


def test_no_dominance_when_the_lead_is_inside_the_margin():
    rows = [
        _cpu("Barely", 200, single=102, multi=1020),
        _cpu("Other", 300, single=100, multi=1000),
    ]
    scoring.score_candidates(rows, "cpu", ["gaming"])
    assert scoring.find_dominant(rows, "name") is None


def test_no_dominance_when_any_candidate_is_unscored():
    """An unmeasured row is exactly where a better part would hide."""
    rows = [
        _cpu("Best", 200, single=140, multi=1400),
        _cpu("Worse", 300, single=100, multi=1000),
        _cpu("Unknown", 250),
    ]
    scoring.score_candidates(rows, "cpu", ["gaming"])
    assert scoring.find_dominant(rows, "name") is None


def test_no_dominance_when_any_candidate_is_unpriced():
    rows = [
        _cpu("Best", 200, single=140, multi=1400),
        _cpu("NoPrice", None, single=100, multi=1000),
    ]
    scoring.score_candidates(rows, "cpu", ["gaming"])
    assert scoring.find_dominant(rows, "name") is None


def test_no_dominance_in_a_single_candidate_set():
    """One candidate is not a comparison, and _ensure_candidates already
    guarantees the set is non-empty."""
    rows = [_cpu("Only", 200, single=140, multi=1400)]
    scoring.score_candidates(rows, "cpu", ["gaming"])
    assert scoring.find_dominant(rows, "name") is None


def test_dominance_respects_the_kill_switch(monkeypatch):
    rows = [
        _cpu("Best", 200, single=140, multi=1400),
        _cpu("Worse", 300, single=100, multi=1000),
    ]
    scoring.score_candidates(rows, "cpu", ["gaming"])
    monkeypatch.setattr(scoring, "DOMINANCE_SKIP_ENABLED", False)
    assert scoring.find_dominant(rows, "name") is None


def test_dominance_works_on_gpu_rows_via_the_name_key():
    rows = [
        _gpu("RTX 5070 Ti", 750, timespy=26000, speedway=6500, port_royal=17000),
        _gpu("RTX 5060", 900, timespy=18000, speedway=4200, port_royal=11000),
    ]
    scoring.score_candidates(rows, "gpu", ["gaming"])
    dominant = scoring.find_dominant(rows, "chipset")
    assert dominant is not None
    assert dominant.name == "RTX 5070 Ti"
