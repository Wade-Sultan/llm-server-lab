from __future__ import annotations

import json
from typing import Any


def _key(value: Any) -> str:
    """Stable comparison key — field values include lists (ddr_generation,
    supported_features), which aren't hashable."""
    return json.dumps(value, sort_keys=True)


def reconcile(
    per_source: list[tuple[str, dict, dict]],
) -> tuple[dict, dict, dict, list[str]]:
    """Merge per-source extractions into one item.

    Input is (source_url, values, provenance) per source, in search rank
    order. Per field the modal value wins; ties break toward the earliest
    (highest-ranked) source, so the merge is deterministic. Returns
    (extracted_fields, field_provenance, extraction_confidence, source_urls).

    extraction_confidence records {"agreement", "n_sources"} for every field,
    plus a {url: value} "values" map only where sources actually disagreed —
    that map is what the admin review UI surfaces as a conflict flag."""
    source_urls = [url for url, _, _ in per_source]

    field_order: list[str] = []
    for _, values, _ in per_source:
        for field in values:
            if field not in field_order:
                field_order.append(field)

    extracted: dict[str, Any] = {}
    provenance: dict[str, dict] = {}
    confidence: dict[str, dict] = {}

    for field in field_order:
        reporting = [
            (url, values[field], prov[field])
            for url, values, prov in per_source
            if field in values
        ]

        counts: dict[str, int] = {}
        for _, value, _ in reporting:
            counts[_key(value)] = counts.get(_key(value), 0) + 1
        # max() keeps the first-seen key on ties — i.e. the highest-ranked source.
        modal_key = max(counts, key=counts.get)  # type: ignore[arg-type]

        chosen_url, chosen_value, chosen_prov = next(
            entry for entry in reporting if _key(entry[1]) == modal_key
        )
        extracted[field] = chosen_value
        provenance[field] = chosen_prov

        confidence[field] = {
            "agreement": counts[modal_key] / len(reporting),
            "n_sources": len(reporting),
        }
        if len(counts) > 1:
            confidence[field]["values"] = {url: value for url, value, _ in reporting}

    return extracted, provenance, confidence, source_urls
