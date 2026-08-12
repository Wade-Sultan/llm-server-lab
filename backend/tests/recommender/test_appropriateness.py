"""
Unit tests for app.services.recommender.appropriateness.

The properties worth pinning down are the ones that make this a *metric* rather
than a score-shaped number:

  * Sufficiency gates. A part that cannot do the job must not be rescued by
    being cheap, because a weighted sum would let it and a product must not.
  * Missing signals are reported, not fabricated. A metric with no requirement
    to check against silently returning 1.0 would inflate every unmatched
    profile and make the aggregate meaningless.
  * The motherboard's asymmetry. Too few PCIe slots is unrecoverable three steps
    later; paying for IPMI is only money. The scores must differ accordingly.
  * Feedback is specific. GEPA reflects on the text, so a test that only checked
    the float would let the useful half rot.
"""

from __future__ import annotations

import pytest

from app.services.recommender import appropriateness as ap


def _gpu(chipset, price, vram):
    return {"chipset": chipset, "street_price_usd": price, "vram_gb": vram}


def _cpu(name, price, cores, perf=None):
    row = {"name": name, "street_price_usd": price, "cores": cores}
    if perf is not None:
        row["perf_score"] = perf
    return row


def _board(name, price, slots=1, **extra):
    return {
        "name": name,
        "street_price_usd": price,
        "pcie_x16_slots": slots,
        **extra,
    }


# ---------------------------------------------------------------------------
# Shared curve behaviour
# ---------------------------------------------------------------------------


def test_shortfall_is_steep_not_linear():
    """90% of a requirement is not 90% good."""
    assert ap._shortfall_score(10, 10) == 1.0
    assert ap._shortfall_score(9, 10) == pytest.approx(0.6)
    assert ap._shortfall_score(7.5, 10) == pytest.approx(0.0)
    assert ap._shortfall_score(5, 10) == 0.0


def test_shortfall_returns_none_when_nothing_to_check():
    """Unknown requirement and unknown attribute both mean 'says nothing'."""
    assert ap._shortfall_score(16, None) is None
    assert ap._shortfall_score(None, 16) is None


def test_free_headroom_costs_nothing():
    """Spending a bit above the minimum buys longevity and is not a mistake."""
    eff, _ = ap._efficiency_from_price(
        chosen_price=110, cheapest_sufficient=100, slot_budget=100
    )
    assert eff == 1.0


def test_large_overspend_is_penalized():
    eff, detail = ap._efficiency_from_price(
        chosen_price=200, cheapest_sufficient=100, slot_budget=100
    )
    assert eff < 1.0
    assert detail["excess_usd"] == 100.0


def test_spending_the_whole_slot_budget_twice_over_scores_zero():
    eff, _ = ap._efficiency_from_price(
        chosen_price=250, cheapest_sufficient=100, slot_budget=100
    )
    assert eff == 0.0


# ---------------------------------------------------------------------------
# GPU
# ---------------------------------------------------------------------------


def test_gpu_below_vram_floor_scores_near_zero_however_cheap():
    """The gate: a card the model does not fit on is not a bargain."""
    candidates = [_gpu("A", 300, 8), _gpu("B", 900, 24)]
    result = ap.gpu_appropriateness(
        candidates, "A", min_vram_gb=24, slot_budget_usd=900
    )
    assert result.sufficiency == 0.0
    assert result.score == 0.0
    assert "does not run slowly" in result.feedback


def test_gpu_meeting_the_floor_at_the_cheapest_price_scores_one():
    candidates = [_gpu("A", 300, 8), _gpu("B", 900, 24)]
    result = ap.gpu_appropriateness(
        candidates, "B", min_vram_gb=24, slot_budget_usd=900
    )
    assert result.score == 1.0


def test_gpu_overshoot_is_priced_against_the_cheapest_sufficient_option():
    candidates = [_gpu("Enough", 700, 24), _gpu("Excess", 1900, 32)]
    result = ap.gpu_appropriateness(
        candidates, "Excess", min_vram_gb=24, slot_budget_usd=900
    )
    assert result.sufficiency == 1.0
    assert result.efficiency < 1.0
    assert "Enough" in result.feedback
    assert result.detail["excess_usd"] == 1200.0


def test_gpu_without_a_floor_reports_the_missing_signal():
    """No requirement to check means sufficiency was not measured — say so."""
    candidates = [_gpu("A", 300, 8), _gpu("B", 900, 24)]
    result = ap.gpu_appropriateness(candidates, "A", slot_budget_usd=900)
    assert "sufficiency" in result.missing_signals
    assert result.is_informative is False


def test_gpu_out_of_set_pick_scores_zero():
    candidates = [_gpu("A", 300, 8)]
    result = ap.gpu_appropriateness(candidates, "RTX 9090", min_vram_gb=8)
    assert result.score == 0.0
    assert result.detail["out_of_set"] is True
    assert "not in the candidate list" in result.feedback


def test_gpu_name_matching_is_case_insensitive():
    """chosen_name comes back from an LLM; letter case is not a wrong answer."""
    candidates = [_gpu("RTX 5070 Ti", 750, 16)]
    result = ap.gpu_appropriateness(candidates, "rtx 5070 ti", min_vram_gb=16)
    assert result.score == 1.0


# ---------------------------------------------------------------------------
# CPU
# ---------------------------------------------------------------------------


def test_cpu_below_core_floor_is_penalized():
    candidates = [_cpu("Small", 150, 4), _cpu("Big", 400, 16)]
    result = ap.cpu_appropriateness(candidates, "Small", min_cores=16)
    assert result.sufficiency == 0.0
    assert "core-bound" in result.feedback


def test_cpu_overspend_with_real_performance_gain_reads_as_defensible():
    candidates = [
        _cpu("Cheap", 200, 8, perf=0.6),
        _cpu("Fast", 320, 8, perf=1.0),
    ]
    result = ap.cpu_appropriateness(
        candidates, "Fast", min_cores=8, slot_budget_usd=400
    )
    assert result.detail["perf_gain_vs_cheapest"] == pytest.approx(0.667, abs=1e-3)
    assert "reasonable trade" in result.feedback


def test_cpu_overspend_without_performance_gain_is_questioned():
    candidates = [
        _cpu("Cheap", 200, 8, perf=0.98),
        _cpu("Pricey", 340, 8, perf=1.0),
    ]
    result = ap.cpu_appropriateness(
        candidates, "Pricey", min_cores=8, slot_budget_usd=400
    )
    assert "Weigh whether" in result.feedback


def test_cpu_missing_perf_score_is_reported():
    """Decisions recorded before scoring.py landed have no perf_score."""
    candidates = [_cpu("Cheap", 200, 8), _cpu("Pricey", 340, 8)]
    result = ap.cpu_appropriateness(
        candidates, "Pricey", min_cores=8, slot_budget_usd=400
    )
    assert "perf_score" in result.missing_signals


# ---------------------------------------------------------------------------
# Motherboard — the asymmetry
# ---------------------------------------------------------------------------


def test_board_with_too_few_slots_scores_zero_for_a_multi_gpu_profile():
    candidates = [_board("Narrow", 150, slots=1), _board("Wide", 400, slots=2)]
    result = ap.motherboard_appropriateness(
        candidates, "Narrow", needs_multi_gpu=True, slot_budget_usd=400
    )
    assert result.sufficiency == 0.0
    assert "hard ceiling" in result.feedback
    assert "nothing downstream can" in result.feedback


def test_slot_under_provisioning_is_punished_harder_than_slot_waste():
    """The asymmetry _step_gpu documents: too few slots is unrecoverable."""
    candidates = [_board("Narrow", 150, slots=1), _board("Wide", 260, slots=2)]

    too_few = ap.motherboard_appropriateness(
        candidates, "Narrow", needs_multi_gpu=True, slot_budget_usd=200
    )
    too_many = ap.motherboard_appropriateness(
        candidates, "Wide", needs_multi_gpu=False, slot_budget_usd=200
    )
    assert too_few.score < too_many.score
    assert too_few.score == 0.0
    assert too_many.score > 0.0


def test_board_paying_for_unused_workstation_features_is_flagged():
    candidates = [
        _board("Consumer", 200, slots=1),
        _board("Workstation", 700, slots=1, has_ipmi=True, memory_channels=8),
    ]
    result = ap.motherboard_appropriateness(
        candidates, "Workstation", slot_budget_usd=250, is_server_profile=False
    )
    assert "IPMI" in result.feedback
    assert result.detail["unused_workstation_features"]
    assert result.efficiency < 1.0


def test_workstation_features_are_not_flagged_on_a_server_profile():
    candidates = [_board("Workstation", 700, slots=2, has_ipmi=True, supports_ecc=True)]
    result = ap.motherboard_appropriateness(
        candidates,
        "Workstation",
        needs_multi_gpu=True,
        needs_ecc=True,
        is_server_profile=True,
    )
    assert result.detail["unused_workstation_features"] == []
    assert result.score == 1.0


def test_board_missing_required_ecc_fails_sufficiency():
    candidates = [
        _board("NoEcc", 200, slots=1),
        _board("Ecc", 300, slots=1, supports_ecc=True),
    ]
    result = ap.motherboard_appropriateness(candidates, "NoEcc", needs_ecc=True)
    assert result.sufficiency == 0.0
    assert "cannot be added later" in result.feedback


# ---------------------------------------------------------------------------
# GEPA adapter
# ---------------------------------------------------------------------------


def test_gepa_metric_returns_score_and_feedback():
    """A bare float would reduce GEPA to random search over instructions."""
    import json

    import dspy

    metric = ap.make_gepa_metric("DecideGPU")
    gold = dspy.Example(
        candidates=json.dumps([_gpu("A", 300, 8), _gpu("B", 900, 24)]),
        min_vram_gb=24,
        slot_budget_usd=900,
    )
    pred = dspy.Prediction(gpu_chipset="B")

    out = metric(gold, pred, None, None, None)
    assert out.score == 1.0
    assert isinstance(out.feedback, str) and out.feedback


def test_gepa_metric_reads_the_right_output_field_per_module():
    """GPU rows are keyed 'chipset' but the signature emits 'gpu_chipset';
    boards are keyed 'name' but emit 'motherboard_name'. Collapsing either
    would score every choice as out-of-set."""
    import json

    import dspy

    metric = ap.make_gepa_metric("DecideMotherboard")
    gold = dspy.Example(candidates=json.dumps([_board("B550", 150, slots=1)]))
    pred = dspy.Prediction(motherboard_name="B550")

    out = metric(gold, pred, None, None, None)
    assert out.score > 0.0


def test_gepa_metric_rejects_an_unknown_module():
    with pytest.raises(ValueError, match="no appropriateness metric"):
        ap.make_gepa_metric("DecideToaster")
