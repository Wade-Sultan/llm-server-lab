from __future__ import annotations

# Below this, a SerpAPI result is treated as "probably not the part we
# searched for" (wrong SKU, bundle, accessory, ...) and excluded from the
# price stats — but every raw result is still stored with its score, so this
# threshold isn't a hard data-loss point, just a stats-inclusion gate. The
# eventual ML classifier trains on exactly this signal plus the ones it
# rejects.
SIMILARITY_THRESHOLD = 45.0


def similarity(part_title: str, result_title: str) -> float:
    """Lightweight title-consistency score in [0, 100]. Token-sort so word
    order differences ("RTX 5080 MSI Gaming X Trio" vs "MSI Gaming X Trio RTX
    5080") don't tank the score."""
    from rapidfuzz import fuzz  # lazy: pricing ETL job only, off the API import path

    return fuzz.token_sort_ratio(part_title, result_title)
