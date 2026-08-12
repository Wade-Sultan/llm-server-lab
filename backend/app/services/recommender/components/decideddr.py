from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import dspy

from app.services.recommender.optimizing import run_gepa

WEIGHTS_PATH = Path(__file__).parent / "weights" / "decideddr.json"


class DDRSelection(dspy.Signature):
    """
    Decide whether this build should use DDR4 or DDR5 memory.

    This is a platform-level decision that constrains CPU and motherboard
    selection downstream. Pick the generation that gives the best value for
    the user's actual workload — not just the newest option.

    Consider:
    - DDR4: cheaper RAM and motherboards, still perfectly capable for gaming
      and general productivity. Makes sense on tight budgets or workloads
      that don't benefit from extra bandwidth.
    - DDR5: higher bandwidth, future-proof, required for newer platforms.
      Worth it for content creation, heavy multitasking, ML workloads, or
      when the budget comfortably supports it.

    Output a reconsideration_threshold: a concrete budget or use-case change
    at which the other generation becomes worth considering.
    """

    use_cases: str = dspy.InputField(
        desc="User's selected use cases, preferences, and Q&A answers as a summary"
    )
    budget_total: int = dspy.InputField(
        desc="Total build budget in USD; -1 means the user has set no budget at all"
    )
    candidates: str = dspy.InputField(
        desc="JSON list with DDR generation options. Fields: ddr_gen, "
        "avg_ram_price_usd, avg_cpu_price_usd, avg_mobo_price_usd, cpu_count"
    )

    # Add 'ddr6' to the desc when DDR6 parts exist.
    ddr_gen: str = dspy.OutputField(desc="'ddr4' or 'ddr5'")
    reason: str = dspy.OutputField(
        desc="1-2 sentences. Lead with the value argument for this workload and budget."
    )
    reconsideration_threshold: str = dspy.OutputField(
        desc="One sentence stating when the other DDR generation becomes worth considering. "
        "Be specific — mention a dollar figure or use-case change."
    )


class DecideDDR(dspy.Module):
    # Telemetry metadata — bump signature_version only when this signature's
    # input/output fields change shape (GEPA needs a consistent field shape).
    signature_name = "DecideDDR"
    signature_version = 1
    category = "ddr"
    output_name_field = "ddr_gen"

    def __init__(self) -> None:
        self.chain = dspy.ChainOfThought(DDRSelection)

    def forward(
        self,
        use_cases: str,
        budget_total: int,
        candidates: str,
    ) -> dspy.Prediction:
        return self.chain(
            use_cases=use_cases,
            budget_total=budget_total,
            candidates=candidates,
        )


@lru_cache(maxsize=1)
def load_program() -> DecideDDR:
    """Load saved weights if available, otherwise return a fresh module."""
    module = DecideDDR()
    if WEIGHTS_PATH.exists():
        module.load(str(WEIGHTS_PATH))
    return module


def optimize(
    trainset: list[dspy.Example],
    metric,
    save: bool = True,
    **gepa_kwargs,
) -> DecideDDR:
    """
    Run GEPA to optimize the DDR selection prompt.

    Each Example in trainset should have:
        - use_cases (str)
        - budget_total (int)
        - candidates (str)
        - ddr_gen (str)            ← gold standard pick ('ddr4' or 'ddr5')
        - reason (str)             ← optional, used by metric
        - reconsideration_threshold (str)  ← optional, used by metric

    The metric follows GEPA's protocol — (gold, pred, trace, pred_name,
    pred_trace) returning dspy.Prediction(score, feedback). The feedback text is
    what GEPA reflects on to rewrite this instruction; a bare float reduces it
    to random search.

    No appropriateness metric ships for this step: DDR generation is a platform
    choice whose consequences land on the CPU, board and RAM steps rather than
    here, so it is best judged by their scores rather than by one of its own.
    """
    return run_gepa(
        DecideDDR(), trainset, metric, WEIGHTS_PATH, save=save, **gepa_kwargs
    )
