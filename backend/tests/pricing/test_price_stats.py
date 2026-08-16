"""How a bag of shopping prices becomes one street price.

Pure-logic tests — no DB, no SerpAPI. The case that motivated all of this is
`test_prebuilt_does_not_drag_the_price_up`: a search for a GPU returns whole
gaming PCs containing it, and averaging them in is what produced the wildly
overestimated prices this module now exists to prevent.
"""

from __future__ import annotations

from app.services.pricing_etl import stats


def _usd(*prices: float) -> list[float]:
    return list(prices)


# --- The overestimation case --------------------------------------------------


def test_prebuilt_does_not_drag_the_price_up():
    # Nine listings of an ~$1000 card, plus two prebuilt desktops containing it
    # that got past the title filter.
    prices = _usd(999, 1019, 989, 1049, 1009, 1029, 979, 1039, 999, 2899, 3499)

    result = stats.compute_stats(prices)

    assert result is not None
    # The old behaviour — the mean of everything — lands near $1400.
    assert sum(prices) / len(prices) > 1350
    # The new one stays where the actual cards are.
    assert 97000 <= result.applied_cents <= 103000
    assert result.n_kept == 9
    assert set(result.rejected.values()) == {stats.REASON_HIGH_OUTLIER}


def test_accessories_do_not_drag_the_price_down():
    # A $60 bracket and a $25 cable alongside the real thing.
    prices = _usd(999, 1019, 989, 1009, 1029, 60, 25)

    result = stats.compute_stats(prices)

    assert result is not None
    assert 99000 <= result.applied_cents <= 103000
    assert result.n_kept == 5
    assert set(result.rejected.values()) == {stats.REASON_LOW_OUTLIER}


def test_applied_price_is_the_median_not_the_mean():
    # Right-skewed but spread widely enough that nothing is trimmed: the
    # remaining difference between the two figures is the skew itself, and the
    # applied price must be the one the skew does not move.
    prices = _usd(500, 520, 560, 600, 700)

    result = stats.compute_stats(prices)

    assert result is not None
    assert result.applied_cents == result.median_cents
    assert result.applied_cents < result.mean_cents


# --- The MSRP anchor ----------------------------------------------------------


def test_anchor_rejects_implausible_prices_in_both_directions():
    prices = _usd(48, 199, 209, 219, 1499)

    result = stats.compute_stats(prices, anchor_cents=20000)  # $200 MSRP

    assert result is not None
    assert result.rejected[0] == stats.REASON_BELOW_ANCHOR
    assert result.rejected[4] == stats.REASON_ABOVE_ANCHOR
    assert result.n_kept == 3
    assert result.applied_cents == 20900


def test_anchor_leaves_real_market_movement_alone():
    # A card selling at 1.9x MSRP in a shortage is a real price, not an
    # outlier: the anchor is aimed at whole systems, not at the market.
    prices = _usd(1130, 1140, 1150)

    result = stats.compute_stats(prices, anchor_cents=59900)  # $599 MSRP

    assert result is not None
    assert result.n_kept == 3
    assert result.applied_cents == 114000


def test_missing_anchor_still_trims():
    # Groups (gpu_chipsets, ram_groups, ...) have no msrp_cents, so the robust
    # band has to carry the whole job on its own.
    prices = _usd(199, 205, 210, 202, 4999)

    result = stats.compute_stats(prices, anchor_cents=None)

    assert result is not None
    assert result.n_kept == 4
    assert result.rejected[4] == stats.REASON_HIGH_OUTLIER


# --- Thin and degenerate samples ----------------------------------------------


def test_thin_sample_records_stats_but_applies_no_price():
    result = stats.compute_stats(_usd(499, 505))

    assert result is not None
    assert result.n_kept == 2
    # Two listings that agree can be two listings of the same wrong thing.
    assert result.applied_cents is None
    assert result.median_cents == 50200


def test_exactly_the_minimum_sample_applies():
    result = stats.compute_stats(_usd(499, 502, 505))

    assert result is not None
    assert result.n_kept == stats.MIN_SAMPLES_TO_APPLY
    assert result.applied_cents == 50200


def test_no_results_at_all_is_none():
    assert stats.compute_stats([]) is None


def test_everything_rejected_by_the_anchor_is_none():
    assert stats.compute_stats(_usd(4999, 5999), anchor_cents=10000) is None


def test_non_positive_prices_are_rejected_not_averaged():
    result = stats.compute_stats(_usd(0, -5, 199, 205, 202, 210))

    assert result is not None
    assert result.rejected[0] == stats.REASON_NON_POSITIVE
    assert result.rejected[1] == stats.REASON_NON_POSITIVE
    assert result.n_kept == 4


def test_identical_prices_survive_a_zero_mad():
    # MAD is 0 here, so a naive band collapses onto the median and throws away
    # a perfectly good sample.
    result = stats.compute_stats(_usd(299, 299, 299, 299))

    assert result is not None
    assert result.n_kept == 4
    assert result.applied_cents == 29900
    assert result.stddev_cents == 0


def test_kept_indices_line_up_with_the_input():
    prices = _usd(999, 3499, 1019, 989, 1009, 1029)

    result = stats.compute_stats(prices)

    assert result is not None
    assert 1 not in result.kept_indices
    assert result.kept_indices == [0, 2, 3, 4, 5]
    # Every input index is accounted for exactly once, which is what lets the
    # runner annotate raw_results without re-deriving anything.
    assert sorted(result.kept_indices + list(result.rejected)) == list(
        range(len(prices))
    )


def test_single_result_has_no_stddev():
    result = stats.compute_stats(_usd(750))

    assert result is not None
    assert result.stddev_cents is None
    assert result.applied_cents is None  # below MIN_SAMPLES_TO_APPLY
    assert result.median_cents == 75000
