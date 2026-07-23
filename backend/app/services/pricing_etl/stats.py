from __future__ import annotations

import statistics
from dataclasses import dataclass


@dataclass
class PriceStats:
    mean_cents: int
    min_cents: int
    max_cents: int
    median_cents: int
    stddev_cents: int | None  # None for a single sample — stdev is undefined


def _to_cents(dollars: float) -> int:
    # SerpAPI reports extracted_price in dollars; every other price field in
    # this schema (street_price_cents, msrp_cents, ...) is in cents.
    return round(dollars * 100)


def compute_stats(prices_usd: list[float]) -> PriceStats | None:
    if not prices_usd:
        return None
    cents = [_to_cents(p) for p in prices_usd]
    return PriceStats(
        mean_cents=round(statistics.fmean(cents)),
        min_cents=min(cents),
        max_cents=max(cents),
        median_cents=round(statistics.median(cents)),
        stddev_cents=round(statistics.pstdev(cents)) if len(cents) > 1 else None,
    )
