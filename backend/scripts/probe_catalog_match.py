"""Probe what the catalog actually matches for a phrase a user might type.

    uv run python scripts/probe_catalog_match.py "R6" "Tarkov" "Resolve"
    uv run python scripts/probe_catalog_match.py --top 5 "llama 70b"

WHY THIS EXISTS. services/recommender/catalog_match.py accepts a match only
inside _MATCH_MAX_DISTANCE (0.45 cosine), and services/embeddings/store.py's
search defaults to 0.65. Both numbers were picked by judgment, not measurement.
This is how you measure them: run the phrasings your users actually type and
look at where the real match sits versus where the cutoff is.

WHAT TO LOOK FOR:

  * A correct match ABOVE the cutoff (distance > 0.45) is a silent miss. The
    build proceeds with no requirements attached and nothing anywhere says so.
    Common for abbreviations and community nicknames — the embedded text is
    built from the catalog's own title and genre, so "R6" only resolves to
    Rainbow Six Siege if the embedding model already knew that association.
  * A WRONG match below the cutoff is worse than a miss: it attaches real
    numeric floors to the build under a title the user never named, and those
    numbers then look authoritative in the prompt.
  * Everything clustered around 0.5-0.6 means the cutoff is doing nothing useful
    and the source text in embeddings/text.py needs more to grip.

Costs one embedding call per phrase. Reads only.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.core.db import AsyncSessionLocal
from app.models.ai_catalog import AIModel
from app.models.games_catalog import Game
from app.models.software_catalog import Software
from app.services.embeddings import client, store
from app.services.recommender.catalog_match import _MATCH_MAX_DISTANCE

# entity_type -> (model, display attribute)
_LABELS = {
    "game": (Game, "title"),
    "software": (Software, "name"),
    "ai_model": (AIModel, "name"),
}


async def _label(db, entity_type: str, entity_id) -> str:
    entry = _LABELS.get(entity_type)
    if entry is None:
        return str(entity_id)
    model, attr = entry
    row = (
        await db.execute(select(model).where(model.id == entity_id))
    ).scalar_one_or_none()
    return getattr(row, attr, str(entity_id)) if row is not None else "(deleted)"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phrases", nargs="+", help="what a user might type")
    parser.add_argument("--top", type=int, default=3, help="hits to show per phrase")
    parser.add_argument(
        "--max-distance",
        type=float,
        default=0.9,
        help="how far out to look; deliberately loose so near-misses are visible",
    )
    args = parser.parse_args()

    if not client.is_configured():
        print("OPENAI_API_KEY is not set — nothing to probe with.", file=sys.stderr)
        return 1

    async with AsyncSessionLocal() as db:
        counts = await db.execute(
            select(store.Embedding.entity_type, store.Embedding.entity_id)
        )
        by_type: dict[str, int] = {}
        for entity_type, _ in counts:
            by_type[entity_type] = by_type.get(entity_type, 0) + 1
        catalog_total = sum(
            by_type.get(t, 0) for t in ("game", "software", "ai_model")
        )
        print(
            "Catalog vectors: "
            + ", ".join(
                f"{t}={by_type.get(t, 0)}" for t in ("game", "software", "ai_model")
            )
        )
        if catalog_total == 0:
            print(
                "\nNo catalog vectors at all — run `python -m app.jobs.embeddings` "
                "first.\nEvery probe below would return nothing regardless of "
                "phrasing.",
                file=sys.stderr,
            )
            return 1
        print(f"Accept cutoff in catalog_match: {_MATCH_MAX_DISTANCE}\n")

        for phrase in args.phrases:
            hits = await store.search_catalog(
                db, phrase, limit=args.top, max_distance=args.max_distance
            )
            print(f"{phrase!r}")
            if not hits:
                print("    (nothing within --max-distance at all)\n")
                continue
            for hit in hits:
                name = await _label(db, hit.entity_type, hit.entity_id)
                accepted = hit.distance <= _MATCH_MAX_DISTANCE
                mark = "ACCEPTED" if accepted else "  missed"
                print(
                    f"    [{mark}] {hit.distance:.3f}  "
                    f"{hit.entity_type}: {name}"
                )
            best = hits[0]
            if best.distance > _MATCH_MAX_DISTANCE:
                print(
                    f"    -> nearest is {best.distance:.3f}, outside the "
                    f"{_MATCH_MAX_DISTANCE} cutoff. If that match is correct, "
                    f"this phrasing is a silent miss in production."
                )
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
