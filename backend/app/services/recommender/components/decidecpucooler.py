from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import dspy

WEIGHTS_PATH = Path(__file__).parent / "weights" / "decidecpucooler.json"


class CoolerSelection(dspy.Signature):
    """
    Select the best CPU cooler for this build from the given candidates.

    All candidates already clear the CPU's TDP and socket requirements.
    Focus on value: avoid recommending a 360mm AIO when the CPU runs cool
    and the use case doesn't demand silence or overclocking headroom.
    """

    use_cases: str = dspy.InputField(desc="User's use cases and preferences summary")
    cpu_name: str = dspy.InputField(desc="The chosen CPU (drives TDP expectations)")
    cpu_tdp_w: int = dspy.InputField(desc="CPU TDP in watts")
    budget_ceiling: int = dspy.InputField(desc="Maximum to spend on cooler in USD")
    candidates: str = dspy.InputField(
        desc="JSON list of compatible coolers. Fields: name, type, max_tdp_w, "
        "noise_db, height_mm, street_price_usd"
    )

    cooler_name: str = dspy.OutputField(desc="Exact product name of the chosen cooler")
    reason: str = dspy.OutputField(
        desc="1-2 sentences. Explain why this cooling level is appropriate, "
        "not just that it fits."
    )
    reconsideration_threshold: str = dspy.OutputField(
        desc="Price point at which upgrading to a better cooler becomes worth it."
    )


class DecideCPUCooler(dspy.Module):
    # Telemetry metadata — bump signature_version only when this signature's
    # input/output fields change shape (GEPA needs a consistent field shape).
    signature_name = "DecideCPUCooler"
    signature_version = 1
    category = "cooler"
    output_name_field = "cooler_name"

    def __init__(self) -> None:
        self.chain = dspy.ChainOfThought(CoolerSelection)

    def forward(self, use_cases, cpu_name, cpu_tdp_w, budget_ceiling, candidates):
        return self.chain(
            use_cases=use_cases,
            cpu_name=cpu_name,
            cpu_tdp_w=cpu_tdp_w,
            budget_ceiling=budget_ceiling,
            candidates=candidates,
        )


@lru_cache(maxsize=1)
def load_program() -> DecideCPUCooler:
    module = DecideCPUCooler()
    if WEIGHTS_PATH.exists():
        module.load(str(WEIGHTS_PATH))
    return module


def optimize(trainset, metric, num_iterations=10, save=True) -> DecideCPUCooler:
    module = DecideCPUCooler()
    optimizer = dspy.GEPA(metric=metric, num_iterations=num_iterations)
    optimized = optimizer.compile(module, trainset=trainset)
    if save:
        WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        optimized.save(str(WEIGHTS_PATH))
    return optimized
