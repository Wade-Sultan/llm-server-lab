from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import dspy

WEIGHTS_PATH = Path(__file__).parent / "weights" / "decidecpu.json"


class CPUSelection(dspy.Signature):
    """
    Select the best CPU for this build from the given candidates.

    Prioritize genuine value for the user's actual workload. Resist the pull
    toward the newest or most impressive option — recommend what actually fits
    the use case and budget. If a lower-tier CPU covers the workload well and
    costs meaningfully less, choose it and say so.

    Output a reconsideration_threshold: a concrete price boundary at which
    the calculus changes. E.g. 'If the Ryzen 7 7800X3D drops below $280,
    upgrade; above that the gaming premium isn't justified for this workload.'

    For server and workstation builds the deciding specs are different ones.
    pcie_lanes caps how many GPUs and NVMe drives can run at full width — four
    x16 GPUs need 64 lanes from the CPU regardless of what the board's slot
    count suggests — and memory_channels sets bandwidth, which is what HPC and
    CPU-side inference are actually limited by. A high-clocking desktop part
    with 28 lanes and 2 channels is the wrong answer for those builds however
    well it benchmarks, and a workstation part is the wrong answer for a
    single-GPU desktop that would never use the lanes it pays for.
    """

    use_cases: str = dspy.InputField(
        desc="User's selected use cases, preferences, and Q&A answers as a summary"
    )
    budget_total: int = dspy.InputField(
        desc="Total build budget in USD; -1 means the user has set no budget at all"
    )
    cpu_budget_ceiling: int = dspy.InputField(
        desc="Maximum to spend on CPU in USD; -1 means no ceiling — the user has said cost is not a constraint"
    )
    candidates: str = dspy.InputField(
        desc="JSON list of compatible CPUs with street prices. Fields: name, brand, "
        "cores, threads, base_clock_ghz, boost_clock_ghz, tdp_w, socket, "
        "ddr_gen, has_integrated_graphics, pcie_lanes, memory_channels, "
        "supports_ecc, street_price_usd. The last three are null on consumer "
        "desktop parts — that absence is meaningful, not missing data."
    )

    cpu_name: str = dspy.OutputField(
        desc="Exact product name of the chosen CPU, matching a name from candidates"
    )
    reason: str = dspy.OutputField(
        desc="1-2 sentences. Lead with the value argument, not the spec argument."
    )
    reconsideration_threshold: str = dspy.OutputField(
        desc="One sentence stating the price point at which a different candidate "
        "becomes worth considering. Be specific with a dollar figure."
    )


class DecideCPU(dspy.Module):
    # Telemetry metadata — bump signature_version only when this signature's
    # input/output fields change shape (GEPA needs a consistent field shape).
    signature_name = "DecideCPU"
    signature_version = 1
    category = "cpu"
    output_name_field = "cpu_name"

    def __init__(self) -> None:
        self.chain = dspy.ChainOfThought(CPUSelection)

    def forward(
        self,
        use_cases: str,
        budget_total: int,
        cpu_budget_ceiling: int,
        candidates: str,
    ) -> dspy.Prediction:
        return self.chain(
            use_cases=use_cases,
            budget_total=budget_total,
            cpu_budget_ceiling=cpu_budget_ceiling,
            candidates=candidates,
        )


@lru_cache(maxsize=1)
def load_program() -> DecideCPU:
    """Load saved weights if available, otherwise return a fresh module."""
    module = DecideCPU()
    if WEIGHTS_PATH.exists():
        module.load(str(WEIGHTS_PATH))
    return module


def optimize(
    trainset: list[dspy.Example],
    metric,
    num_iterations: int = 10,
    save: bool = True,
) -> DecideCPU:
    """
    Run GEPA to optimize the CPU selection prompt.

    Each Example in trainset should have:
        - use_cases (str)
        - budget_total (int)
        - cpu_budget_ceiling (int)
        - candidates (str)
        - cpu_name (str)           ← gold standard pick
        - reason (str)             ← optional, used by metric
        - reconsideration_threshold (str)  ← optional, used by metric

    The metric function receives (example, prediction, trace=None) and
    returns a float in [0, 1].  Include price-gap reasoning quality in
    the metric — not just name correctness.
    """
    module = DecideCPU()
    optimizer = dspy.GEPA(metric=metric, num_iterations=num_iterations)
    optimized = optimizer.compile(module, trainset=trainset)

    if save:
        WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        optimized.save(str(WEIGHTS_PATH))

    return optimized
