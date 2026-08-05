from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.crud.components import _normalize

_FUZZY_THRESHOLD = 90.0


@dataclass
class CatalogCandidate:
    id: uuid.UUID
    name: str
    model_number: str | None = None


def match_item(
    name: str,
    model_number: str | None,
    candidates: list[CatalogCandidate],
) -> tuple[uuid.UUID | None, str | None, float | None]:
    """Deterministic dedup against the existing catalog.

    Tiers: normalized-exact -> model_number -> rapidfuzz fuzzy (>= 90).
    Returns (candidate_id, match_method, match_score); all None when nothing
    clears. 'embedding' is reserved for a future pgvector tier."""
    if not candidates:
        return None, None, None

    target = _normalize(name)
    for c in candidates:
        if _normalize(c.name) == target:
            return c.id, "exact", 1.0

    if model_number:
        mn = model_number.strip().lower()
        for c in candidates:
            if c.model_number and c.model_number.strip().lower() == mn:
                return c.id, "model_number", 1.0

    from rapidfuzz import fuzz  # lazy: discovery job only

    best_id: uuid.UUID | None = None
    best_score = 0.0
    for c in candidates:
        score = fuzz.WRatio(target, _normalize(c.name))
        if score > best_score:
            best_id, best_score = c.id, score
    if best_id is not None and best_score >= _FUZZY_THRESHOLD:
        return best_id, "fuzzy", best_score / 100.0

    return None, None, None


def match_columns(
    category: str, matched_id: uuid.UUID | None
) -> dict[str, uuid.UUID | None]:
    """Route a dedup hit onto the right discovered_items FK column.

    Three catalogs can be matched against and each has its own column, because
    a match is a foreign key and gpu_chipsets / ai_models are not pc_parts
    rows. All three keys are always returned (not just the populated one) so an
    ON CONFLICT DO UPDATE refresh clears a stale match from a previous run
    rather than leaving it behind.
    """
    return {
        "matched_part_id": matched_id
        if category not in ("gpu_chipset", "ai_model")
        else None,
        "matched_chipset_id": matched_id if category == "gpu_chipset" else None,
        "matched_ai_model_id": matched_id if category == "ai_model" else None,
    }


def filter_new_names(
    names: list[str], known_normalized: set[str], limit: int
) -> list[str]:
    """Narrow enumerated sweep candidates to the ones worth paying to extract:
    drop in-sweep repeats and anything already in the catalog or review queue,
    preserving order, then cap.

    Exact-on-normalized only — deliberately weaker than match_item(). Fuzzy
    would skip candidates *before* any extraction happens, and WRatio scores a
    new SKU against its predecessor right at the 90 threshold ("RTX 5080 Super"
    vs "RTX 5080"), so a fuzzy pre-filter would silently discard exactly the
    launches a sweep exists to catch. Near-misses cost one extraction each and
    are caught afterwards by match_item(), which flags them for the reviewer
    instead of dropping them.
    """
    seen: set[str] = set()
    fresh: list[str] = []
    for name in names:
        key = _normalize(name)
        if not key or key in known_normalized or key in seen:
            continue
        seen.add(key)
        fresh.append(name)
        if len(fresh) == limit:
            break
    return fresh
