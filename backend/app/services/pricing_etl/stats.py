from __future__ import annotations

import statistics
from dataclasses import dataclass, field

# Turning a bag of Google Shopping prices into one street price.
#
# The mean over everything that passed the title filter — what this module used
# to apply — is systematically too high, because the contamination in shopping
# results is one-sided. A search for "RTX 5080" returns the card at ~$1000
# alongside whole gaming PCs containing it at $2500-$4000; nothing sells for
# *less* than a fifth of the part's price in the same result set except
# accessories. One $3500 prebuilt in twelve results moves the mean by ~$200 and
# the median by nothing, which is the entire reason the applied figure is now
# the median of a trimmed sample rather than a raw mean.
#
# Three defences, cheapest first:
#   1. title filtering, before this module ever sees a price
#      (title_match.exclusion_reason) — kills the prebuilts and bundles by name
#   2. an MSRP anchor, when the target has one — kills anything implausible in
#      absolute terms
#   3. a robust band around the median (below) — kills what is left, without
#      assuming the sample is normal or that we know the true price already
#
# Every rejection is reported per-sample rather than just dropped, so
# price_checks.raw_results keeps the negative examples the eventual legitimacy
# classifier trains on.

# Below this many usable samples the median is not worth trusting, so the check
# is recorded but no price is applied — the part keeps whatever it had and gets
# re-checked next cycle. Two results that agree can easily be two listings of
# the same wrong thing.
MIN_SAMPLES_TO_APPLY = 3

# Absolute plausibility band around a known MSRP. Wide on purpose: GPUs really
# do sell at 2x MSRP in a shortage, and end-of-life parts really do sell at 40%
# of it. This is aimed at the $3500 prebuilt, not at market movement.
ANCHOR_MAX_RATIO = 2.5
ANCHOR_MIN_RATIO = 0.35

# Robust band around the sample median. The MAD term adapts to how tight the
# sample actually is; the ratio terms are the backstop for a sample so spread
# out that 3 MADs still admits a prebuilt. Whichever is tighter wins.
MAD_K = 3.0
_MAD_TO_SIGMA = 1.4826  # makes MAD comparable to a standard deviation
BAND_MAX_RATIO = 1.75
BAND_MIN_RATIO = 0.45

# Rejection reasons recorded per sample.
REASON_NON_POSITIVE = "non_positive_price"
REASON_ABOVE_ANCHOR = "above_msrp_anchor"
REASON_BELOW_ANCHOR = "below_msrp_anchor"
REASON_HIGH_OUTLIER = "high_outlier"
REASON_LOW_OUTLIER = "low_outlier"


@dataclass
class PriceStats:
    """Stats over the kept sample, plus what was thrown away and why.

    applied_cents is the only field the ETL writes to street_price_cents; it is
    None when the sample was too thin to trust, which is deliberately different
    from "no results at all" (compute_stats returns None for that).
    """

    applied_cents: int | None

    mean_cents: int
    min_cents: int
    max_cents: int
    median_cents: int
    stddev_cents: int | None  # None for a single sample — stdev is undefined

    n_considered: int
    n_kept: int

    # Indices into the input list, so the caller can annotate the raw results
    # it already built without re-deriving the arithmetic.
    kept_indices: list[int] = field(default_factory=list)
    rejected: dict[int, str] = field(default_factory=dict)


def _to_cents(dollars: float) -> int:
    # SerpAPI reports extracted_price in dollars; every other price field in
    # this schema (street_price_cents, msrp_cents, ...) is in cents.
    return round(dollars * 100)


def _median(values: list[int]) -> int:
    return round(statistics.median(values))


def compute_stats(
    prices_usd: list[float], *, anchor_cents: int | None = None
) -> PriceStats | None:
    """Reduce one target's shopping results to a street price.

    anchor_cents is the target's MSRP when it has one. Only pc_parts carry an
    MSRP — the group tables (gpu_chipsets, psu_groups, ...) don't — so this is
    an extra defence where it's available, never a requirement.

    Returns None when there is nothing to measure at all.
    """
    if not prices_usd:
        return None

    cents = [_to_cents(p) for p in prices_usd]
    rejected: dict[int, str] = {}

    candidates: list[int] = []  # indices still in play
    for i, c in enumerate(cents):
        if c <= 0:
            rejected[i] = REASON_NON_POSITIVE
            continue
        if anchor_cents:
            if c > anchor_cents * ANCHOR_MAX_RATIO:
                rejected[i] = REASON_ABOVE_ANCHOR
                continue
            if c < anchor_cents * ANCHOR_MIN_RATIO:
                rejected[i] = REASON_BELOW_ANCHOR
                continue
        candidates.append(i)

    if not candidates:
        return None

    kept = _trim_outliers(cents, candidates, rejected)
    kept_values = [cents[i] for i in kept]

    return PriceStats(
        applied_cents=(
            _median(kept_values) if len(kept_values) >= MIN_SAMPLES_TO_APPLY else None
        ),
        mean_cents=round(statistics.fmean(kept_values)),
        min_cents=min(kept_values),
        max_cents=max(kept_values),
        median_cents=_median(kept_values),
        stddev_cents=(
            round(statistics.pstdev(kept_values)) if len(kept_values) > 1 else None
        ),
        n_considered=len(cents),
        n_kept=len(kept_values),
        kept_indices=kept,
        rejected=rejected,
    )


def _trim_outliers(
    cents: list[int], candidates: list[int], rejected: dict[int, str]
) -> list[int]:
    """Keep the candidates inside a robust band around their own median,
    recording why each one that falls out did. Mutates `rejected`.

    The median and MAD are computed over the candidates *including* the
    outliers, which is the point of using them: both survive up to half the
    sample being garbage, so the band doesn't need a clean sample to be drawn
    from — unlike a mean-and-stddev band, which the prebuilts would widen far
    enough to admit themselves.
    """
    values = [cents[i] for i in candidates]
    median = _median(values)
    if median <= 0:  # pathological; nothing sensible to band around
        return candidates

    hi = median * BAND_MAX_RATIO
    lo = median * BAND_MIN_RATIO

    mad = _median([abs(v - median) for v in values])
    if mad > 0:
        spread = MAD_K * _MAD_TO_SIGMA * mad
        # Tighter of the two bounds. With a tight sample the MAD term wins and
        # trims aggressively; with a wildly spread one the ratio term caps it.
        hi = min(hi, median + spread)
        lo = max(lo, median - spread)

    kept: list[int] = []
    for i in candidates:
        if cents[i] > hi:
            rejected[i] = REASON_HIGH_OUTLIER
        elif cents[i] < lo:
            rejected[i] = REASON_LOW_OUTLIER
        else:
            kept.append(i)

    # A band that rejects everything means the median itself sat outside it,
    # which can only happen through a rounding edge; fall back to the untrimmed
    # candidates rather than reporting no usable price.
    if not kept:
        for i in candidates:
            rejected.pop(i, None)
        return candidates
    return kept
