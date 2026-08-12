"""
appropriateness.py
==================
Quality metrics for the three Decide* steps worth optimizing — CPU, GPU and
motherboard — expressed as GEPA metrics and as pure functions that can be run
retroactively over recorded telemetry.

THE QUESTION EACH METRIC ASKS, in two halves:

  Sufficiency  Does this part actually do the job the profile describes? A build
               that cannot run the user's game is a failed build, no matter what
               it cost. Scored steeply: 90% of the requirement is not 90% good.

  Efficiency   Having cleared the bar, how much money went above it? Some
               headroom is right — the cheapest part that exactly meets today's
               requirement is fragile, because games get heavier and monitors
               get bigger. Far above it is the user overpaying for capability
               the profile never uses.

  score = sufficiency * efficiency

A PRODUCT, NOT A WEIGHTED SUM, because the two are not tradeable. Being cheap
cannot compensate for being unable to run the workload, and a weighted sum would
let it: 0.5 * insufficient + 0.5 * very cheap still passes. The product makes
sufficiency a gate, which is what it actually is.

WHY OVERSHOOT IS PRICED, NOT MEASURED IN PERFORMANCE. A part 40% faster than
needed for 5% more money is not a mistake — it is a good buy. What makes
overshoot bad is specifically the dollars it takes away from the rest of the
build. So efficiency is computed as money spent above the cheapest candidate
that would also have been sufficient, normalized against the slot's budget.
That figure is computable entirely from the candidate set, which
module_decisions already snapshots verbatim — which is what lets these metrics
be run over builds that have already happened rather than needing a fresh
collection campaign.

WHY perf_score CANNOT CARRY THIS ALONE. The perf_score that scoring.py injects
is *set-relative*: best-in-candidate-set is 1.0 by construction. A $1,600 GPU in
a $2,000 build and a $300 GPU in a $600 build both score 1.0. It is the right
signal for ranking within a set and useless as an absolute anchor, so it is used
here only to compare candidates against each other, never to judge "enough".

THE MOTHERBOARD IS NOT A PERFORMANCE METRIC. It is an option-preservation one.
Its sufficiency asks whether the board kept open the expansion the profile
needs — above all, PCIe x16 slots, because dspy_pipeline._step_gpu takes the
board's slot count as a hard ceiling on gpu_count and there is no going back to
a wider board from there. Under-provisioning slots is unrecoverable; paying for
IPMI and eight memory channels on a gaming build is merely wasteful. The curves
reflect that asymmetry rather than treating both as symmetric error.

EVERY METRIC RETURNS TEXT, NOT JUST A NUMBER. GEPA's whole mechanism is
reflecting on *why* a score was low in order to rewrite an instruction; a metric
that returns a bare float reduces it to random search. See `Appropriateness.
feedback` and the adapters at the bottom.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# --- Curve shape -----------------------------------------------------------

# Below the requirement, score decays this many times faster than the shortfall.
# At 4.0, hitting 90% of a requirement scores 0.6 and 75% scores 0.0 — steep
# enough that "nearly enough" is not treated as nearly right, gradual enough
# that GEPA still gets a gradient to climb instead of a cliff of zeros.
_SHORTFALL_STEEPNESS = 4.0

# Money above the cheapest sufficient candidate, as a fraction of the slot
# budget, that costs nothing. This is the "slightly above is good" band: real
# headroom for a workload that grows, bought deliberately.
_FREE_HEADROOM = 0.25

# Beyond the free band, efficiency decays linearly and reaches 0 when the
# overspend equals the entire slot budget — i.e. the step spent twice what it
# needed to.
_TOTAL_WASTE_FRACTION = 1.0


@dataclass
class Appropriateness:
    """One part choice, scored.

    `detail` carries the intermediate figures so the offline distribution script
    can report on them without recomputing, and so a surprising score can be
    explained without re-running anything.
    """

    score: float
    sufficiency: float
    efficiency: float
    feedback: str
    detail: dict[str, Any] = field(default_factory=dict)
    # Signals that were unavailable. A metric computed with no requirements and
    # no benchmark data is measuring almost nothing, and callers — especially
    # the offline script — need to know that rather than average it in.
    missing_signals: list[str] = field(default_factory=list)

    @property
    def is_informative(self) -> bool:
        """Whether enough was known to make this score worth believing."""
        return "sufficiency" not in self.missing_signals


# ---------------------------------------------------------------------------
# Shared machinery
# ---------------------------------------------------------------------------


def _num(value: Any) -> float | None:
    """Coerce to a positive float, treating 0/None/junk as 'not recorded'."""
    if isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _shortfall_score(actual: float | None, required: float | None) -> float | None:
    """1.0 when the requirement is met, decaying steeply below it.

    None when the requirement is unknown (not a constraint) or the candidate
    does not record the attribute — both mean "this dimension says nothing",
    which is different from "this dimension says zero".
    """
    if required is None or actual is None:
        return None
    if actual >= required:
        return 1.0
    ratio = actual / required
    return max(0.0, 1.0 - _SHORTFALL_STEEPNESS * (1.0 - ratio))


def _find(rows: list[dict], name_key: str, name: str | None) -> dict | None:
    """The candidate row the step actually chose, matched case-insensitively.

    Tolerant matching because chosen_name comes back from an LLM and the
    pipeline's own resolution step is equally tolerant — a metric that scored a
    successful build as 0 over letter case would be measuring the wrong thing.
    """
    if not name:
        return None
    target = name.strip().lower()
    for row in rows:
        value = row.get(name_key)
        if isinstance(value, str) and value.strip().lower() == target:
            return row
    return None


def _efficiency_from_price(
    chosen_price: float | None,
    cheapest_sufficient: float | None,
    slot_budget: float | None,
) -> tuple[float, dict[str, Any]]:
    """How much of the slot's budget went above the cheapest adequate option.

    Normalized against the slot budget rather than against the cheapest price,
    because the same $200 overspend means something different in a $300 slot and
    a $900 one. Falls back to the cheapest price as the denominator when no slot
    budget is recorded, which is the conservative direction (a smaller
    denominator means a larger penalty).
    """
    detail: dict[str, Any] = {}
    if chosen_price is None or cheapest_sufficient is None:
        return 1.0, detail

    excess = chosen_price - cheapest_sufficient
    detail["excess_usd"] = round(excess, 2)
    detail["cheapest_sufficient_usd"] = round(cheapest_sufficient, 2)
    if excess <= 0:
        return 1.0, detail

    denominator = slot_budget or cheapest_sufficient
    if not denominator or denominator <= 0:
        return 1.0, detail

    fraction = excess / denominator
    detail["excess_fraction_of_budget"] = round(fraction, 3)
    if fraction <= _FREE_HEADROOM:
        return 1.0, detail

    span = _TOTAL_WASTE_FRACTION - _FREE_HEADROOM
    return max(0.0, 1.0 - (fraction - _FREE_HEADROOM) / span), detail


def _combine(
    parts: dict[str, float | None],
) -> tuple[float | None, list[str]]:
    """Fold per-dimension sufficiency scores into one, weakest-link style.

    The minimum rather than the mean: a GPU with ample VRAM and half the
    required compute is not "average". Any single unmet hard requirement makes
    the part unsuitable, and the metric has to say so.

    Returns (score, unmet_dimension_names). None when no dimension was known.
    """
    known = {k: v for k, v in parts.items() if v is not None}
    if not known:
        return None, []
    worst = min(known.values())
    unmet = [k for k, v in known.items() if v < 1.0]
    return worst, unmet


# ---------------------------------------------------------------------------
# GPU
# ---------------------------------------------------------------------------


def gpu_appropriateness(
    candidates: list[dict],
    chosen_name: str | None,
    *,
    min_vram_gb: int | None = None,
    slot_budget_usd: float | None = None,
    matched_titles: list[str] | None = None,
) -> Appropriateness:
    """Score a chipset choice for a profile.

    `min_vram_gb` is the binding constraint for AI and high-resolution creative
    work, and comes from catalog_match's resolution of what the user named. It
    is a hard floor in a way frame rate is not: a model that does not fit in
    VRAM does not run slowly, it does not run.

    KNOWN GAP: catalog_match also resolves `required_features` (bf16, fp8,
    tensor cores — the feature floors an AI workload needs), and this does not
    check them. It cannot: db/queries.py::_serialize_gpu_chipset does not put
    `supported_features` on the candidate rows, so the data is absent from the
    snapshot this scores against. Closing it means adding that field to the
    serializer first — at which point old telemetry still will not have it, so
    the check has to report a missing signal rather than fail.
    """
    missing: list[str] = []
    detail: dict[str, Any] = {}
    chosen = _find(candidates, "chipset", chosen_name)

    if chosen is None:
        return Appropriateness(
            score=0.0,
            sufficiency=0.0,
            efficiency=0.0,
            feedback=(
                f"Chose {chosen_name!r}, which is not in the candidate list. The "
                f"choice must be one of the chipsets provided — an out-of-set "
                f"pick cannot be bought and the build falls back to a reference."
            ),
            detail={"out_of_set": True},
            missing_signals=["sufficiency", "efficiency"],
        )

    chosen_price = _num(chosen.get("street_price_usd"))
    chosen_vram = _num(chosen.get("vram_gb"))

    dims: dict[str, float | None] = {
        "vram": _shortfall_score(chosen_vram, _num(min_vram_gb)),
    }
    if min_vram_gb is None:
        missing.append("min_vram_gb")

    sufficiency, unmet = _combine(dims)
    if sufficiency is None:
        # No floor to check against. Say so rather than scoring 1.0, which would
        # read as "verified sufficient" and quietly inflate every unmatched
        # profile's score.
        missing.append("sufficiency")
        sufficiency = 1.0

    def _is_sufficient(row: dict) -> bool:
        if min_vram_gb is None:
            return True
        vram = _num(row.get("vram_gb"))
        return vram is not None and vram >= min_vram_gb

    priced = [
        (p, r)
        for r in candidates
        if (p := _num(r.get("street_price_usd"))) is not None and _is_sufficient(r)
    ]
    cheapest = min((p for p, _ in priced), default=None)
    efficiency, price_detail = _efficiency_from_price(
        chosen_price, cheapest, slot_budget_usd
    )
    detail.update(price_detail)
    detail["chosen_vram_gb"] = chosen_vram
    detail["min_vram_gb"] = min_vram_gb

    # Which candidate the money should have gone to, for the feedback line.
    alternative = None
    if cheapest is not None and chosen_price is not None and chosen_price > cheapest:
        alternative = next((r for p, r in priced if p == cheapest), None)

    feedback = _gpu_feedback(
        chosen, chosen_price, unmet, min_vram_gb, alternative, cheapest, matched_titles
    )
    return Appropriateness(
        score=sufficiency * efficiency,
        sufficiency=sufficiency,
        efficiency=efficiency,
        feedback=feedback,
        detail=detail,
        missing_signals=missing,
    )


def _gpu_feedback(
    chosen: dict,
    chosen_price: float | None,
    unmet: list[str],
    min_vram_gb: int | None,
    alternative: dict | None,
    cheapest: float | None,
    matched_titles: list[str] | None,
) -> str:
    name = chosen.get("chipset", "the chosen chipset")
    price_str = f"${chosen_price:,.0f}" if chosen_price else "an unrecorded price"
    source = f" (required by {', '.join(matched_titles)})" if matched_titles else ""

    if "vram" in unmet:
        return (
            f"{name} has {chosen.get('vram_gb')}GB of VRAM but the workload needs "
            f"at least {min_vram_gb}GB{source}. This is a hard floor, not a "
            f"performance preference — a model or scene that does not fit in VRAM "
            f"does not run slowly, it fails to run. Choose a chipset meeting the "
            f"VRAM floor even if it is slower or costs more elsewhere in the build."
        )

    if alternative is not None and chosen_price and cheapest:
        excess = chosen_price - cheapest
        return (
            f"{name} at {price_str} clears the requirements, but so does "
            f"{alternative.get('chipset')} at ${cheapest:,.0f} — "
            f"${excess:,.0f} of the budget bought capability this profile does not "
            f"call for{source}. Headroom is worth paying for; this much of it "
            f"takes money away from slots that still have to be filled. Prefer the "
            f"cheaper sufficient option unless the extra directly serves a stated "
            f"need, and use reconsideration_threshold to say what the extra spend "
            f"would buy."
        )

    return (
        f"{name} at {price_str} is a good fit: it meets the workload's "
        f"requirements{source} without a materially cheaper candidate that would "
        f"also have met them."
    )


# ---------------------------------------------------------------------------
# CPU
# ---------------------------------------------------------------------------


def cpu_appropriateness(
    candidates: list[dict],
    chosen_name: str | None,
    *,
    min_cores: int | None = None,
    slot_budget_usd: float | None = None,
    matched_titles: list[str] | None = None,
) -> Appropriateness:
    """Score a CPU choice.

    Note what is deliberately NOT checked here: socket and DDR compatibility.
    Those are enforced by the candidate query before the model ever sees the
    list, so scoring them would measure the SQL rather than the decision and
    would report a flat 1.0 forever.
    """
    missing: list[str] = []
    detail: dict[str, Any] = {}
    chosen = _find(candidates, "name", chosen_name)

    if chosen is None:
        return Appropriateness(
            score=0.0,
            sufficiency=0.0,
            efficiency=0.0,
            feedback=(
                f"Chose {chosen_name!r}, which is not in the candidate list. The "
                f"choice must be one of the CPUs provided; an out-of-set pick "
                f"cannot be resolved to a catalog part and fails the build."
            ),
            detail={"out_of_set": True},
            missing_signals=["sufficiency", "efficiency"],
        )

    chosen_price = _num(chosen.get("street_price_usd"))
    dims: dict[str, float | None] = {
        "cores": _shortfall_score(_num(chosen.get("cores")), _num(min_cores)),
    }
    if min_cores is None:
        missing.append("min_cores")

    sufficiency, unmet = _combine(dims)
    if sufficiency is None:
        missing.append("sufficiency")
        sufficiency = 1.0

    def _is_sufficient(row: dict) -> bool:
        if min_cores is None:
            return True
        cores = _num(row.get("cores"))
        return cores is not None and cores >= min_cores

    priced = [
        (p, r)
        for r in candidates
        if (p := _num(r.get("street_price_usd"))) is not None and _is_sufficient(r)
    ]
    cheapest = min((p for p, _ in priced), default=None)
    efficiency, price_detail = _efficiency_from_price(
        chosen_price, cheapest, slot_budget_usd
    )
    detail.update(price_detail)
    detail["chosen_cores"] = chosen.get("cores")
    detail["min_cores"] = min_cores

    # perf_score is set-relative, so it cannot say "enough" — but it can say
    # whether the extra money bought extra speed, which is what makes an
    # overspend defensible or not.
    chosen_perf = _num(chosen.get("perf_score"))
    alternative = None
    if cheapest is not None and chosen_price is not None and chosen_price > cheapest:
        alternative = next((r for p, r in priced if p == cheapest), None)
    if chosen_perf is not None and alternative is not None:
        alt_perf = _num(alternative.get("perf_score"))
        if alt_perf:
            detail["perf_gain_vs_cheapest"] = round(chosen_perf / alt_perf - 1, 3)
    else:
        missing.append("perf_score")

    feedback = _cpu_feedback(
        chosen,
        chosen_price,
        unmet,
        min_cores,
        alternative,
        cheapest,
        detail.get("perf_gain_vs_cheapest"),
        matched_titles,
    )
    return Appropriateness(
        score=sufficiency * efficiency,
        sufficiency=sufficiency,
        efficiency=efficiency,
        feedback=feedback,
        detail=detail,
        missing_signals=missing,
    )


def _cpu_feedback(
    chosen: dict,
    chosen_price: float | None,
    unmet: list[str],
    min_cores: int | None,
    alternative: dict | None,
    cheapest: float | None,
    perf_gain: float | None,
    matched_titles: list[str] | None,
) -> str:
    name = chosen.get("name", "the chosen CPU")
    price_str = f"${chosen_price:,.0f}" if chosen_price else "an unrecorded price"
    source = f" (required by {', '.join(matched_titles)})" if matched_titles else ""

    if "cores" in unmet:
        return (
            f"{name} has {chosen.get('cores')} cores against a floor of "
            f"{min_cores}{source}. The workload the user described will be "
            f"core-bound on this part. Choose a CPU at or above the core floor."
        )

    if alternative is not None and chosen_price and cheapest:
        excess = chosen_price - cheapest
        gain = (
            f" for {perf_gain:.0%} more performance on this workload"
            if perf_gain is not None
            else ""
        )
        verdict = (
            "That is a reasonable trade."
            if perf_gain is not None and perf_gain >= 0.15
            else "Weigh whether that is worth the money taken from other slots."
        )
        return (
            f"{name} at {price_str} costs ${excess:,.0f} more than "
            f"{alternative.get('name')} at ${cheapest:,.0f}{gain}. {verdict} "
            f"Remember the budget ceiling is a maximum, not a target — spending "
            f"under it is a good outcome when the workload does not need the rest."
        )

    return (
        f"{name} at {price_str} is well matched: it meets the workload's core "
        f"requirements{source} and no cheaper candidate would also have."
    )


# ---------------------------------------------------------------------------
# Motherboard
# ---------------------------------------------------------------------------


def motherboard_appropriateness(
    candidates: list[dict],
    chosen_name: str | None,
    *,
    needs_multi_gpu: bool = False,
    target_gpu_count: int | None = None,
    needs_ecc: bool = False,
    slot_budget_usd: float | None = None,
    is_server_profile: bool = False,
) -> Appropriateness:
    """Score a board choice on the options it preserved, not on performance.

    THE ASYMMETRY THIS ENCODES. dspy_pipeline._step_gpu caps gpu_count at the
    board's pcie_x16_slots, and the GPU step runs three steps later — there is no
    path back to a wider board. So a board with too few slots does not make the
    build slightly worse, it makes a whole class of build unreachable, and the
    sufficiency term is what carries that. Paying for IPMI and eight memory
    channels on a gaming build is ordinary waste and lands in efficiency.
    """
    missing: list[str] = []
    detail: dict[str, Any] = {}
    chosen = _find(candidates, "name", chosen_name)

    if chosen is None:
        return Appropriateness(
            score=0.0,
            sufficiency=0.0,
            efficiency=0.0,
            feedback=(
                f"Chose {chosen_name!r}, which is not in the candidate list. The "
                f"choice must be one of the motherboards provided."
            ),
            detail={"out_of_set": True},
            missing_signals=["sufficiency", "efficiency"],
        )

    required_slots = target_gpu_count or (2 if needs_multi_gpu else 1)
    chosen_price = _num(chosen.get("street_price_usd"))
    chosen_slots = _num(chosen.get("pcie_x16_slots")) or 1.0

    dims: dict[str, float | None] = {
        "pcie_x16_slots": _shortfall_score(chosen_slots, float(required_slots)),
    }
    if needs_ecc:
        dims["ecc"] = 1.0 if chosen.get("supports_ecc") else 0.0

    sufficiency, unmet = _combine(dims)
    if sufficiency is None:  # pragma: no cover - slots always yield a score
        missing.append("sufficiency")
        sufficiency = 1.0

    def _is_sufficient(row: dict) -> bool:
        slots = _num(row.get("pcie_x16_slots")) or 1.0
        if slots < required_slots:
            return False
        return bool(row.get("supports_ecc")) if needs_ecc else True

    priced = [
        (p, r)
        for r in candidates
        if (p := _num(r.get("street_price_usd"))) is not None and _is_sufficient(r)
    ]
    cheapest = min((p for p, _ in priced), default=None)
    efficiency, price_detail = _efficiency_from_price(
        chosen_price, cheapest, slot_budget_usd
    )
    detail.update(price_detail)
    detail["required_x16_slots"] = required_slots
    detail["chosen_x16_slots"] = int(chosen_slots)

    # Workstation features on a consumer profile are the board-specific waste
    # this metric exists to catch, and they are invisible to a pure price
    # comparison when the whole candidate set happens to be expensive.
    luxuries = []
    if not is_server_profile:
        if chosen.get("has_ipmi"):
            luxuries.append("IPMI remote management")
        if (_num(chosen.get("memory_channels")) or 2) > 2:
            luxuries.append("more than two memory channels")
        if chosen.get("supports_ecc") and not needs_ecc:
            luxuries.append("ECC support")
    detail["unused_workstation_features"] = luxuries

    feedback = _motherboard_feedback(
        chosen, chosen_price, unmet, required_slots, luxuries, cheapest, needs_multi_gpu
    )
    return Appropriateness(
        score=sufficiency * efficiency,
        sufficiency=sufficiency,
        efficiency=efficiency,
        feedback=feedback,
        detail=detail,
        missing_signals=missing,
    )


def _motherboard_feedback(
    chosen: dict,
    chosen_price: float | None,
    unmet: list[str],
    required_slots: int,
    luxuries: list[str],
    cheapest: float | None,
    needs_multi_gpu: bool,
) -> str:
    name = chosen.get("name", "the chosen board")
    price_str = f"${chosen_price:,.0f}" if chosen_price else "an unrecorded price"

    if "pcie_x16_slots" in unmet:
        return (
            f"{name} has {chosen.get('pcie_x16_slots')} PCIe x16 slot(s) but this "
            f"profile needs {required_slots}. This is the most expensive mistake "
            f"available at this step: the GPU step runs three steps later and "
            f"takes the board's slot count as a hard ceiling on how many cards "
            f"the build can host, so a narrow board silently forecloses the "
            f"multi-GPU build the user asked for and nothing downstream can "
            f"recover it. When the workload might want more than one card, favour "
            f"the board with the slots."
        )

    if "ecc" in unmet:
        return (
            f"{name} does not support ECC memory, which this profile requires. "
            f"ECC is a platform property — it cannot be added later by choosing "
            f"different RAM."
        )

    if luxuries:
        return (
            f"{name} at {price_str} meets the profile's needs but pays for "
            f"{', '.join(luxuries)}, which a desktop build of this kind never "
            f"uses. Those are server-platform features; on a consumer profile "
            f"they are money that should have gone to the GPU or storage."
            + (
                f" A sufficient board was available at ${cheapest:,.0f}."
                if cheapest and chosen_price and chosen_price > cheapest
                else ""
            )
        )

    if cheapest and chosen_price and chosen_price > cheapest:
        return (
            f"{name} at {price_str} preserves the right options "
            f"({required_slots} x16 slot(s)), but a board meeting the same "
            f"requirements was available at ${cheapest:,.0f}. A motherboard adds "
            f"no performance — beyond connectivity and expansion, money spent "
            f"here buys the build nothing."
        )

    multi = " and keeps the multi-GPU option open" if needs_multi_gpu else ""
    return (
        f"{name} at {price_str} is well matched: it preserves the expansion this "
        f"profile needs{multi} without paying for workstation features it does not."
    )


# ---------------------------------------------------------------------------
# GEPA adapters
# ---------------------------------------------------------------------------

# Decide* module -> (candidate-row name key, scoring function, signature output
# field). The first two differ on purpose and the third differs from both: a GPU
# candidate row is keyed "chipset" while DecideGPU emits "gpu_chipset", and a
# motherboard row is keyed "name" while DecideMotherboard emits
# "motherboard_name". Collapsing any pair would silently score every choice as
# out-of-set.
_METRIC_DISPATCH: dict[str, tuple[str, Any, str]] = {
    "DecideCPU": ("name", cpu_appropriateness, "cpu_name"),
    "DecideGPU": ("chipset", gpu_appropriateness, "gpu_chipset"),
    "DecideMotherboard": ("name", motherboard_appropriateness, "motherboard_name"),
}


def _requirements_from_gold(gold: Any) -> dict[str, Any]:
    """Pull the metric's context off a training Example.

    The trainset builder is responsible for attaching these; every one is
    optional and its absence degrades the metric rather than breaking it (see
    Appropriateness.missing_signals). Read via getattr because a dspy.Example
    exposes its fields as attributes and raises on unknown ones.
    """
    return {
        key: getattr(gold, key, None)
        for key in (
            "min_vram_gb",
            "min_cores",
            "slot_budget_usd",
            "matched_titles",
            "needs_multi_gpu",
            "target_gpu_count",
            "needs_ecc",
            "is_server_profile",
        )
    }


def make_gepa_metric(signature_name: str):
    """Build a GEPA metric for one Decide* module.

    Returns a callable matching GEPA's five-argument protocol and returning
    dspy.Prediction(score, feedback) — the feedback is what GEPA reflects on to
    rewrite the module's instruction, so returning a bare float here would
    reduce the optimizer to random search.

    `pred_name` is accepted and ignored: each Decide* module wraps a single
    ChainOfThought, so there is only ever one predictor to attribute feedback to.
    """
    import inspect
    import json

    import dspy

    entry = _METRIC_DISPATCH.get(signature_name)
    if entry is None:
        raise ValueError(
            f"no appropriateness metric for {signature_name!r} "
            f"(have: {', '.join(sorted(_METRIC_DISPATCH))})"
        )
    _name_key, scorer, output_field = entry

    # Which context keys this particular scorer understands. Computed once at
    # construction rather than per call, and from the real signature rather than
    # from __code__ internals — the scorers take keyword-only arguments, which
    # co_varnames slicing does not describe correctly.
    accepted = {
        name
        for name, p in inspect.signature(scorer).parameters.items()
        if p.kind is inspect.Parameter.KEYWORD_ONLY
    }

    def metric(gold, pred, trace=None, pred_name=None, pred_trace=None):  # noqa: ARG001
        try:
            candidates = json.loads(getattr(gold, "candidates", "") or "[]")
        except (TypeError, ValueError):
            candidates = []
        if not isinstance(candidates, list):
            candidates = []

        chosen = getattr(pred, output_field, None)
        kwargs = {
            k: v
            for k, v in _requirements_from_gold(gold).items()
            if k in accepted and v is not None
        }
        result = scorer(candidates, chosen, **kwargs)
        return dspy.Prediction(score=result.score, feedback=result.feedback)

    metric.__name__ = f"{signature_name}_appropriateness"
    return metric
