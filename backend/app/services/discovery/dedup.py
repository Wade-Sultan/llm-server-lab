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
