"""
scoring.py
==========
Deterministic performance scoring for CPU and GPU candidates, plus the
dominance gate that lets a step skip its LLM call entirely.

WHY THIS EXISTS. The Decide* steps were handed cores, clocks and TDP and left
to infer performance from them. `CPU.benchmark_scores` and
`GPUChipset.benchmark_scores` (JSONB, validated by the pydantic models in
app/models/benchmarks.py) already carry the real numbers and nothing read them.
This module turns them into two fields on every candidate row — `perf_score`
and `perf_per_dollar` — so the model is choosing against measurements instead
of guessing from a spec sheet.

TWO AXES OF DEFENSIVENESS, because benchmark coverage in the catalog is
partial and will stay that way:

  1. A candidate with no usable benchmark data scores None. It is still shown
     to the LLM, still selectable, and simply carries no numbers. Scoring never
     removes a candidate.
  2. The dominance gate (see `find_dominant`) refuses to fire unless *every*
     candidate in the set is scored. A gate that skips the LLM on a partially
     measured set could be skipping past the actual best part.

HOW A SCORE IS BUILT.
  Each workload maps to weights over *axes* (single-thread, multi-thread,
  raster, compute, ...), not over individual benchmarks — because Cinebench
  and Geekbench measure the same axis, and different rows in the catalog carry
  different ones. Within an axis, every benchmark present is normalized against
  the best value in the candidate set (x / max, so ratios are preserved) and
  averaged. The candidate's score is the weighted sum across axes.

  A candidate must carry data for every axis whose weight is at least
  `_MIN_AXIS_WEIGHT`. Without that rule, renormalizing over "the axes we happen
  to have" lets a part with only single-thread data score 1.00 on a rendering
  workload that is decided almost entirely on multi-thread — the exact silent
  mis-ranking this module exists to remove.

  Scores are relative to the candidate set, not absolute. 1.00 means "best in
  this set at this workload", never "fastest part that exists". That is the
  right frame: the set is already budget- and compatibility-filtered, so the
  question is only ever which of *these* to buy.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# An axis carrying at least this much weight must have data behind it before a
# candidate can be scored at all. 0.25 means a workload's dominant axis (and any
# real secondary) is mandatory, while a 0.1-weight tiebreak axis is optional.
_MIN_AXIS_WEIGHT = 0.25

# How much faster the leader must be than the runner-up before the dominance
# gate will skip the LLM. Only consulted once the leader is already at least as
# cheap as every other candidate — see find_dominant.
DOMINANCE_MARGIN = float(os.getenv("RECOMMEND_DOMINANCE_MARGIN", "0.05"))

# Master switch for the skip. Scores are still computed and still injected into
# the candidate JSON when this is off — only the LLM bypass is disabled, which
# makes it safe to turn off in a hurry without changing what the model sees.
DOMINANCE_SKIP_ENABLED = os.getenv("RECOMMEND_DOMINANCE_SKIP", "1") not in (
    "0",
    "false",
    "False",
)


# --- Axis definitions — which benchmark keys measure the same thing -----------
# Keys match the pydantic field names in app/models/benchmarks.py
# (CPUBenchmarkScores / GPUBenchmarkScores). Both models set extra="allow", so
# unknown keys may appear in the JSONB; anything not listed here is ignored
# rather than guessed at.

_CPU_AXES: dict[str, tuple[str, ...]] = {
    "single": ("cinebench_r24_single", "geekbench_6_single"),
    "multi": ("cinebench_r24_multi", "geekbench_6_multi"),
    "igpu": ("night_raid",),
}

_GPU_AXES: dict[str, tuple[str, ...]] = {
    # Rasterization — conventional game rendering.
    "raster": ("timespy",),
    # Ray tracing throughput. Also the best available proxy for GPU-accelerated
    # offline renderers (OptiX, Cycles), which lean on the same RT hardware.
    "ray": ("port_royal",),
    # DX12 Ultimate / mesh shaders — where recent titles actually live.
    "modern": ("speed_way",),
    # General compute: AI/ML, and the GPU half of creative encode/decode work.
    "compute": ("geekbench_6_compute",),
}


# --- Workload → axis weights --------------------------------------------------
# Keyed by BuildRequest.use_cases entries (the frontend's use-case keys, which
# chat_pipeline._PRIMARY_USE_TO_USE_CASE also maps the extracted profile onto).
# Weights within a part type sum to 1.0.

_CPU_WEIGHTS: dict[str, dict[str, float]] = {
    # Games are overwhelmingly latency-bound on a handful of threads.
    "gaming": {"single": 0.75, "multi": 0.25},
    # Encoding a stream while playing is the one gaming-adjacent case with a
    # genuine multi-thread tail.
    "streaming": {"single": 0.45, "multi": 0.55},
    # Timeline scrubbing is single-thread; export is embarrassingly parallel.
    "creator": {"single": 0.35, "multi": 0.65},
    "rendering": {"single": 0.15, "multi": 0.85},
    # The CPU is rarely the binding constraint for local inference, but data
    # loading and tokenization are threaded.
    "aiml": {"single": 0.35, "multi": 0.65},
    "server": {"single": 0.10, "multi": 0.90},
    # Incremental compiles and editor responsiveness are latency-bound; full
    # builds and container fleets are not.
    "dev": {"single": 0.50, "multi": 0.50},
    # DAWs are famously latency-bound — per-track plugin chains run serially.
    "audio": {"single": 0.70, "multi": 0.30},
    "productivity": {"single": 0.65, "multi": 0.35},
    "nas": {"single": 0.30, "multi": 0.70},
}

_GPU_WEIGHTS: dict[str, dict[str, float]] = {
    "gaming": {"raster": 0.60, "modern": 0.30, "ray": 0.10},
    "streaming": {"raster": 0.55, "modern": 0.25, "compute": 0.20},
    "creator": {"compute": 0.50, "raster": 0.30, "modern": 0.20},
    # GPU renderers ride the RT cores, so ray carries real weight here rather
    # than being the tiebreak it is for gaming.
    "rendering": {"compute": 0.55, "ray": 0.30, "raster": 0.15},
    "aiml": {"compute": 1.00},
    "server": {"compute": 1.00},
    "dev": {"raster": 0.60, "compute": 0.40},
    "audio": {"raster": 1.00},
    "productivity": {"raster": 1.00},
    "nas": {"raster": 1.00},
}

_DEFAULT_CPU_WEIGHTS = {"single": 0.55, "multi": 0.45}
_DEFAULT_GPU_WEIGHTS = {"raster": 0.70, "compute": 0.30}


def _blend(weight_sets: list[dict[str, float]]) -> dict[str, float]:
    """Average several axis-weight maps into one, renormalized to sum to 1.

    A BuildRequest can carry several use cases. Averaging their weight maps
    reflects that a machine doing two jobs has to be good at both, rather than
    letting whichever use case happens to sort first decide the ranking.
    """
    if not weight_sets:
        return {}
    merged: dict[str, float] = {}
    for ws in weight_sets:
        for axis, w in ws.items():
            merged[axis] = merged.get(axis, 0.0) + w
    total = sum(merged.values())
    if total <= 0:
        return {}
    return {axis: w / total for axis, w in merged.items()}


def _resolution_adjusted(
    weights: dict[str, float], answers: dict, part_type: str
) -> dict[str, float]:
    """Shift CPU weight from single- toward multi-thread as target resolution rises.

    At 4K the frame rate is pinned by the GPU and the CPU's single-thread lead
    stops translating into frames, so ranking CPUs on it overstates the gap. At
    1080p the reverse holds. GPU weights are untouched — resolution changes how
    much GPU you need, not which GPU characteristic matters.
    """
    if part_type != "cpu":
        return weights
    resolution = str(answers.get("gaming.resolution") or "").lower()
    if resolution not in ("1080p", "4k"):
        return weights
    if "single" not in weights or "multi" not in weights:
        return weights
    shift = 0.10 if resolution == "4k" else -0.10
    single = max(0.0, min(1.0, weights["single"] - shift))
    multi = max(0.0, min(1.0, weights["multi"] + shift))
    total = (
        single
        + multi
        + sum(w for a, w in weights.items() if a not in ("single", "multi"))
    )
    if total <= 0:
        return weights
    adjusted = dict(weights)
    adjusted["single"] = single
    adjusted["multi"] = multi
    return {a: w / total for a, w in adjusted.items()}


def weights_for(
    part_type: str, use_cases: list[str], answers: dict | None = None
) -> dict[str, float]:
    """Axis weights for this part type under these use cases."""
    table = _CPU_WEIGHTS if part_type == "cpu" else _GPU_WEIGHTS
    default = _DEFAULT_CPU_WEIGHTS if part_type == "cpu" else _DEFAULT_GPU_WEIGHTS
    matched = [table[uc] for uc in (use_cases or []) if uc in table]
    weights = _blend(matched) if matched else dict(default)
    return _resolution_adjusted(weights, answers or {}, part_type)


# --- Scoring ------------------------------------------------------------------


def _as_float(value) -> float | None:
    """Coerce a JSONB benchmark value, rejecting anything non-positive.

    Zero and negatives are treated as absent rather than as a real measurement:
    they show up when an importer writes a placeholder, and a genuine benchmark
    score is never <= 0. Letting one through would drag an axis average down as
    if the part had been measured and found slow.
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _axis_maxima(
    rows: list[dict], axes: dict[str, tuple[str, ...]]
) -> dict[str, float]:
    """Best value in the candidate set for each individual benchmark key."""
    maxima: dict[str, float] = {}
    for row in rows:
        scores = row.get("benchmark_scores") or {}
        if not isinstance(scores, dict):
            continue
        for keys in axes.values():
            for key in keys:
                value = _as_float(scores.get(key))
                if value is not None and value > maxima.get(key, 0.0):
                    maxima[key] = value
    return maxima


def _candidate_axis_values(
    scores: dict, axes: dict[str, tuple[str, ...]], maxima: dict[str, float]
) -> dict[str, float]:
    """Normalized 0..1 value per axis for one candidate.

    Every benchmark present for an axis is divided by that benchmark's best in
    the set, then averaged — so a part measured on both Cinebench and Geekbench
    is not double-counted relative to one measured on a single suite.
    """
    values: dict[str, float] = {}
    for axis, keys in axes.items():
        ratios = [
            value / maxima[key]
            for key in keys
            if (value := _as_float(scores.get(key))) is not None
            and maxima.get(key, 0.0) > 0
        ]
        if ratios:
            values[axis] = sum(ratios) / len(ratios)
    return values


def score_candidates(
    rows: list[dict],
    part_type: str,
    use_cases: list[str],
    answers: dict | None = None,
) -> list[dict]:
    """Annotate candidate rows in place with `perf_score` and `perf_per_dollar`.

    `rows` are the dicts the serializers in db/queries.py produce, each expected
    to carry a `benchmark_scores` dict and a `street_price_usd`. Rows that
    cannot be scored get `perf_score: None` and are left otherwise untouched —
    they remain valid candidates the LLM may still choose.

    perf_score is rounded to 3 decimals and perf_per_dollar to 5: these land in
    a prompt, and trailing float noise costs tokens and invites the model to
    read precision that is not there.
    """
    axes = _CPU_AXES if part_type == "cpu" else _GPU_AXES
    weights = weights_for(part_type, use_cases, answers)
    maxima = _axis_maxima(rows, axes)

    # Axes the workload leans on hard enough that missing data disqualifies.
    required = {a for a, w in weights.items() if w >= _MIN_AXIS_WEIGHT}

    for row in rows:
        scores = row.get("benchmark_scores")
        row.pop("benchmark_scores", None)  # internal input, never shown to the LLM
        if not isinstance(scores, dict) or not scores:
            row["perf_score"] = None
            continue

        values = _candidate_axis_values(scores, axes, maxima)
        if not required.issubset(values.keys()):
            row["perf_score"] = None
            continue

        # Renormalize over the axes this candidate actually has. Safe now that
        # every heavily-weighted axis is guaranteed present.
        usable = {a: w for a, w in weights.items() if a in values}
        total_weight = sum(usable.values())
        if total_weight <= 0:
            row["perf_score"] = None
            continue

        score = sum(values[a] * w for a, w in usable.items()) / total_weight
        row["perf_score"] = round(score, 3)

        price = row.get("street_price_usd")
        if isinstance(price, int | float) and price > 0:
            row["perf_per_dollar"] = round(score / price, 5)

    return rows


# --- Dominance gate -----------------------------------------------------------


class Dominant:
    """A candidate that is both cheaper and faster than every alternative.

    Carries the synthesized `reason` / `reconsideration_threshold` the pipeline
    would otherwise have taken from the LLM's prediction, so a skipped step
    still populates state identically to a run one.
    """

    __slots__ = ("row", "name", "reason", "reconsideration_threshold", "margin")

    def __init__(
        self,
        row: dict,
        name: str,
        reason: str,
        reconsideration_threshold: str,
        margin: float,
    ) -> None:
        self.row = row
        self.name = name
        self.reason = reason
        self.reconsideration_threshold = reconsideration_threshold
        self.margin = margin


def find_dominant(
    rows: list[dict], name_key: str, margin: float = DOMINANCE_MARGIN
) -> Dominant | None:
    """Return the candidate that strictly dominates the set, or None.

    "Dominates" is deliberately the strong form — the winner must be

      * at least `margin` faster than the runner-up on the weighted score, AND
      * priced at or below every other candidate,

    which together mean no tradeoff is left for anyone to weigh. That is a much
    higher bar than "clearly ahead on perf-per-dollar", and it is the right one:
    perf-per-dollar leadership still hides a real decision (spend more for more
    machine?), and that decision is exactly what the LLM is for.

    Requires the whole candidate set to be scored. A dominance claim over a
    partially-measured set is not a dominance claim — the unmeasured rows are
    precisely where a better part would hide.

    Fires rarely by construction. That is intended: it is a bypass for cases
    with no judgment in them (a budget ceiling that clipped the set down to one
    real option, or a last-gen part that undercuts a weak current-gen one on
    both axes), not a general-purpose ranker.
    """
    if not DOMINANCE_SKIP_ENABLED or len(rows) < 2:
        return None

    scored = [r for r in rows if isinstance(r.get("perf_score"), int | float)]
    if len(scored) != len(rows):
        return None

    priced = [r for r in rows if isinstance(r.get("street_price_usd"), int | float)]
    if len(priced) != len(rows):
        return None

    ranked = sorted(rows, key=lambda r: r["perf_score"], reverse=True)
    leader, runner_up = ranked[0], ranked[1]

    lead_score = leader["perf_score"]
    next_score = runner_up["perf_score"]
    if next_score <= 0 or lead_score < next_score * (1 + margin):
        return None

    lead_price = leader["street_price_usd"]
    if any(r["street_price_usd"] < lead_price for r in rows if r is not leader):
        return None

    name = leader.get(name_key)
    if not name:
        return None

    achieved = (lead_score / next_score) - 1
    runner_name = runner_up.get(name_key, "the next option")
    runner_price = runner_up["street_price_usd"]

    reason = (
        f"{name} is both the fastest and the cheapest option available for this "
        f"build: it scores {achieved:.0%} above {runner_name} on the benchmarks "
        f"weighted for this workload while costing ${lead_price:,.0f} against "
        f"${runner_price:,.0f}. With no candidate offering more performance or a "
        f"lower price, there is no tradeoff left to weigh."
    )
    threshold = (
        f"Reconsider only if {runner_name} drops below ${lead_price:,.0f}, or if a "
        f"faster part enters the catalog under this build's ceiling — the choice "
        f"here rests on {name} leading on price and performance simultaneously."
    )

    return Dominant(
        row=leader,
        name=name,
        reason=reason,
        reconsideration_threshold=threshold,
        margin=achieved,
    )
