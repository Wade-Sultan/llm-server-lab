"""
optimizing.py
=============
One correct `dspy.GEPA` invocation, shared by every Decide* module's
`optimize()`.

WHY THIS EXISTS. All eleven modules carried the same call:

    dspy.GEPA(metric=metric, num_iterations=num_iterations)

`num_iterations` is not a GEPA parameter, so every one of them raised TypeError
on the first call — the optimizers had never actually been run. Fixing it in
eleven places would have left eleven places to get it wrong again.

WHAT GEPA ACTUALLY NEEDS, none of which the old call supplied:

  A BUDGET. `auto`, `max_full_evals` or `max_metric_calls` — all default to None
  and GEPA refuses to start without exactly one. This is the knob that decides
  what an optimization run costs, so it is deliberately explicit rather than
  buried at a default.

  A REFLECTION LM. GEPA's mechanism is reading the *textual feedback* a metric
  returns and proposing a rewritten instruction from it. That reflection wants a
  strong model, and it does not have to be — should not be — the small model the
  module runs on in production. Optimizing a Gemma-31B prompt using Gemma-31B to
  do the reflecting caps the quality of the rewrite at the reasoning ability of
  the thing being improved.

  A METRIC THAT RETURNS TEXT. See appropriateness.make_gepa_metric. A metric
  returning a bare float turns GEPA into random search over instructions.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import dspy

logger = logging.getLogger(__name__)

# Model that reads the metric's feedback and proposes new instructions. Kept
# separate from RECOMMEND_MODEL on purpose — see the module docstring. Routed
# through OpenRouter like everything else so the spend lands in one dashboard.
REFLECTION_MODEL = os.getenv(
    "GEPA_REFLECTION_MODEL", "openrouter/anthropic/claude-sonnet-4.5"
)

# GEPA's own preset budgets: "light", "medium", "heavy". Light is the right
# default for a first run — it is enough to tell whether the metric is measuring
# anything real, which is the thing worth knowing before paying for heavy.
DEFAULT_BUDGET = os.getenv("GEPA_BUDGET", "light")


def build_reflection_lm(model: str | None = None) -> dspy.LM:
    """The LM GEPA reflects with.

    max_tokens is generous because the output is a rewritten instruction block,
    not a field value — truncating it produces a malformed prompt that GEPA then
    scores badly and discards, which looks like "optimization did nothing".
    """
    from app.core.config import settings

    return dspy.LM(
        model=model or REFLECTION_MODEL,
        api_key=settings.OPENROUTER_API_KEY,
        api_base="https://openrouter.ai/api/v1",
        max_tokens=8192,
        temperature=1.0,
    )


def run_gepa(
    module: dspy.Module,
    trainset: list[dspy.Example],
    metric: Any,
    weights_path: Path,
    *,
    save: bool = True,
    budget: str | None = None,
    max_metric_calls: int | None = None,
    reflection_lm: dspy.LM | None = None,
    valset: list[dspy.Example] | None = None,
    **gepa_kwargs: Any,
) -> dspy.Module:
    """Optimize one module and, by default, persist its weights.

    `budget` is GEPA's `auto` preset; pass `max_metric_calls` instead for an
    exact ceiling. Supplying both is rejected by GEPA rather than silently
    resolved, so this passes exactly one.

    A valset is worth supplying once there is enough data: without one GEPA
    evaluates candidate instructions on the trainset it is proposing from, which
    reports the optimistic number rather than the honest one.
    """
    if not trainset:
        raise ValueError(
            "GEPA needs a non-empty trainset — build one from module_decisions "
            "(see scripts/score_appropriateness.py for reading that table)"
        )

    budget_kwargs: dict[str, Any] = (
        {"max_metric_calls": max_metric_calls}
        if max_metric_calls is not None
        else {"auto": budget or DEFAULT_BUDGET}
    )

    optimizer = dspy.GEPA(
        metric=metric,
        reflection_lm=reflection_lm or build_reflection_lm(),
        track_stats=True,
        **budget_kwargs,
        **gepa_kwargs,
    )

    logger.info(
        "GEPA: optimizing %s over %d example(s) with %s, reflecting with %s",
        type(module).__name__,
        len(trainset),
        budget_kwargs,
        REFLECTION_MODEL,
    )
    compile_kwargs: dict[str, Any] = {"trainset": trainset}
    if valset is not None:
        compile_kwargs["valset"] = valset
    optimized = optimizer.compile(module, **compile_kwargs)

    if save:
        weights_path.parent.mkdir(parents=True, exist_ok=True)
        optimized.save(str(weights_path))
        logger.info("GEPA: saved optimized weights to %s", weights_path)
    return optimized
