"""Score recorded build decisions with the appropriateness metrics.

    uv run python scripts/score_appropriateness.py [--limit N] [--since YYYY-MM-DD]
    uv run python scripts/score_appropriateness.py --worst 15
    uv run python scripts/score_appropriateness.py --category gpu --json out.json

WHY THIS RUNS OFFLINE RATHER THAN NEEDING A COLLECTION CAMPAIGN. module_decisions
stores `candidate_set` as a verbatim JSONB snapshot of exactly what the model was
shown, plus the chosen name and the input state. The appropriateness metrics are
pure functions of those, so every build already recorded can be scored right
now — the eval set is the production history.

READ THE COVERAGE REPORT BEFORE THE SCORES. The metrics degrade rather than fail
when a signal is missing, and two signals arrived recently:

  * `perf_score` on candidate rows — added when scoring.py landed. Decisions
    recorded before then have candidate sets without it.
  * requirement floors (min_vram_gb, min_cores) — these come from catalog_match
    resolving what the user named, which needs embeddings backfilled AND the
    user to have named something matchable.

A decision scored with neither is being judged almost entirely on price, and the
report says so rather than averaging it in silently. A high mean over
low-coverage rows means nothing.

NOTHING HERE WRITES. Safe to run against production.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.core.db import AsyncSessionLocal
from app.models.build_session import BuildSession, ModuleDecision
from app.services.recommender.appropriateness import (
    Appropriateness,
    context_from_requirements,
    cpu_appropriateness,
    gpu_appropriateness,
    motherboard_appropriateness,
)

# module_decisions.category -> (scorer, budget keys to try in input_state).
#
# Two keys per entry because the steps are not consistent: DecideCPU and
# DecideGPU name their ceiling `cpu_budget_ceiling` / `gpu_budget_ceiling`,
# while DecideMotherboard (and most other steps) use the generic
# `budget_ceiling`. Looking for both means this keeps working if a signature is
# renamed toward either convention.
_SCORERS = {
    "cpu": (cpu_appropriateness, ("cpu_budget_ceiling", "budget_ceiling")),
    "gpu": (gpu_appropriateness, ("gpu_budget_ceiling", "budget_ceiling")),
    "motherboard": (
        motherboard_appropriateness,
        ("budget_ceiling", "mobo_budget_ceiling"),
    ),
}


def _slot_budget(input_state: dict | None, keys: tuple[str, ...]) -> float | None:
    """The step's budget ceiling, or None when it was the no-ceiling sentinel.

    -1 is NO_BUDGET_CEILING (the user said cost is not a constraint). Treating it
    as a real $-1 budget would make every overspend look infinite, so it is
    dropped and the efficiency term falls back to the cheapest-price denominator.
    """
    if not isinstance(input_state, dict):
        return None
    for key in keys:
        try:
            f = float(input_state.get(key))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if f > 0:
            return f
    return None


def _score_row(decision: ModuleDecision) -> Appropriateness | None:
    entry = _SCORERS.get(decision.category)
    if entry is None:
        return None
    scorer, budget_keys = entry

    candidates = decision.candidate_set
    if not isinstance(candidates, list) or not candidates:
        return None

    budget = _slot_budget(decision.input_state, budget_keys)
    # The floors the decision was actually made under, snapshotted at decision
    # time. NULL on rows written before migration e3f4a5b6c7d8, and on any build
    # where the user named nothing matchable — both leave sufficiency unmeasured
    # and reported as a missing signal rather than fabricated as passing.
    context = context_from_requirements(
        decision.catalog_requirements, slot_budget_usd=budget
    )
    # Each scorer takes only the keywords it understands; the shared context
    # carries the union of all three.
    accepted = {
        name
        for name, p in inspect.signature(scorer).parameters.items()
        if p.kind is inspect.Parameter.KEYWORD_ONLY
    }
    kwargs = {k: v for k, v in context.items() if k in accepted and v is not None}

    try:
        return scorer(candidates, decision.chosen_name, **kwargs)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"  ! scoring failed for {decision.id}: {exc}", file=sys.stderr)
        return None


async def _load(limit: int, since: datetime | None, category: str | None):
    stmt = (
        select(ModuleDecision, BuildSession.conversation_id)
        .join(BuildSession, ModuleDecision.session_id == BuildSession.id, isouter=True)
        .where(ModuleDecision.category.in_(list(_SCORERS)))
        .order_by(ModuleDecision.created_at.desc())
        .limit(limit)
    )
    if since is not None:
        stmt = stmt.where(ModuleDecision.created_at >= since)
    if category:
        stmt = stmt.where(ModuleDecision.category == category)

    async with AsyncSessionLocal() as db:
        return list((await db.execute(stmt)).all())


def _histogram(scores: list[float], buckets: int = 10) -> str:
    if not scores:
        return "    (no scores)"
    counts = [0] * buckets
    for s in scores:
        idx = min(int(s * buckets), buckets - 1)
        counts[idx] += 1
    widest = max(counts) or 1
    lines = []
    for i, c in enumerate(counts):
        lo, hi = i / buckets, (i + 1) / buckets
        bar = "█" * int(30 * c / widest)
        lines.append(f"    {lo:.1f}–{hi:.1f} {bar:<30} {c}")
    return "\n".join(lines)


def _report(rows: list[tuple[ModuleDecision, Any]], worst_n: int) -> dict:
    by_category: dict[str, list[tuple[ModuleDecision, Appropriateness]]] = defaultdict(
        list
    )
    for decision, _conversation_id in rows:
        result = _score_row(decision)
        if result is not None:
            by_category[decision.category].append((decision, result))

    summary: dict[str, Any] = {}
    for category in sorted(by_category):
        scored = by_category[category]
        results = [r for _, r in scored]
        scores = [r.score for r in results]
        informative = [r for r in results if r.is_informative]

        missing = Counter()
        for r in results:
            for signal in r.missing_signals:
                missing[signal] += 1

        print(
            f"\n{'=' * 70}\n{category.upper()}  ({len(scored)} decisions)\n{'=' * 70}"
        )
        print("\n  COVERAGE")
        print(
            f"    sufficiency measurable: {len(informative)}/{len(results)} "
            f"({len(informative) / len(results):.0%})"
        )
        for signal, count in missing.most_common():
            print(f"    missing {signal}: {count}")
        if not informative:
            print(
                "\n    NOTE: no decision in this sample had a requirement floor to\n"
                "    check against, so every score below is price-efficiency only.\n"
                "    Backfill embeddings and let catalog matching run before\n"
                "    reading anything into the sufficiency numbers."
            )

        print("\n  SCORE DISTRIBUTION")
        print(_histogram(scores))
        print(
            f"\n    mean {statistics.mean(scores):.3f}   "
            f"median {statistics.median(scores):.3f}   "
            f"min {min(scores):.3f}   max {max(scores):.3f}"
        )
        eff = [r.efficiency for r in results]
        suf = [r.sufficiency for r in results]
        print(
            f"    sufficiency mean {statistics.mean(suf):.3f}   "
            f"efficiency mean {statistics.mean(eff):.3f}"
        )

        out_of_set = sum(1 for r in results if r.detail.get("out_of_set"))
        if out_of_set:
            print(
                f"\n    {out_of_set} out-of-set pick(s) — the model named a part that\n"
                f"    was not in its candidate list. These score 0 and are worth\n"
                f"    fixing before optimizing anything else."
            )

        worst = sorted(scored, key=lambda pair: pair[1].score)[:worst_n]
        if worst:
            print(f"\n  {len(worst)} LOWEST-SCORING DECISIONS")
            for decision, result in worst:
                print(f"\n    [{result.score:.3f}] {decision.chosen_name}")
                print(f"      session {decision.session_id}")
                print(f"      {result.feedback}")

        summary[category] = {
            "n": len(scored),
            "mean": statistics.mean(scores),
            "median": statistics.median(scores),
            "sufficiency_mean": statistics.mean(suf),
            "efficiency_mean": statistics.mean(eff),
            "informative": len(informative),
            "out_of_set": out_of_set,
            "missing_signals": dict(missing),
        }
    return summary


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--since", type=str, default=None, help="YYYY-MM-DD")
    parser.add_argument("--category", choices=sorted(_SCORERS), default=None)
    parser.add_argument("--worst", type=int, default=5)
    parser.add_argument("--json", type=str, default=None, help="write summary here")
    args = parser.parse_args()

    since = datetime.fromisoformat(args.since) if args.since else None
    rows = await _load(args.limit, since, args.category)
    if not rows:
        print(
            "No module_decisions rows matched. Either no builds have run yet, or\n"
            "none were run with a BuildRecorder attached."
        )
        return 0

    print(f"Scoring {len(rows)} decision(s)…")
    summary = _report(rows, args.worst)

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(summary, fh, indent=2, default=str)
        print(f"\nSummary written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
