from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import dspy

WEIGHTS_PATH = Path(__file__).parent / "weights" / "decideram.json"


class RAMSelection(dspy.Signature):
    """
    Select the best RAM *group* (spec) for this build from the given candidates.

    Each candidate is a RAM spec (capacity / speed / timings), not a specific
    product — the exact branded kit is resolved deterministically afterwards.
    Choose at the spec level here. Candidates already match the required DDR
    generation. Focus on capacity vs. speed vs. price tradeoffs. For gaming,
    32GB DDR5-5600 is rarely meaningfully better than 32GB DDR5-6000 at $40
    more — call that out.

    Candidates are also already filtered to module types the chosen board
    accepts, so registered/unbuffered compatibility is settled before you see
    them — never reject a candidate on that basis. What is still yours to
    decide: server builds want capacity and enough modules to populate every
    memory channel ahead of raw speed, since bandwidth on those platforms comes
    from channel count, and a kit that leaves half the channels empty wastes
    more performance than a speed bin ever recovers. ECC is worth its premium
    on a machine expected to run unattended and not much otherwise.
    """

    use_cases: str = dspy.InputField(desc="User's use cases and preferences summary")
    ddr_gen: str = dspy.InputField(desc="Required DDR generation (ddr4 or ddr5)")
    budget_ceiling: int = dspy.InputField(
        desc="Maximum to spend on RAM in USD; -1 means no ceiling — the user has said cost is not a constraint"
    )
    candidates: str = dspy.InputField(
        desc="JSON list of RAM groups with the group's street price. Fields: "
        "ram_group, ddr_gen, capacity_gb, speed_mhz, kit_count, cas_latency, "
        "is_ecc, module_type, street_price_usd"
    )

    ram_group: str = dspy.OutputField(
        desc="Exact ram_group label of the chosen spec, matching a candidate"
    )
    reason: str = dspy.OutputField(
        desc="1-2 sentences. Address capacity and speed fit for the use case."
    )
    reconsideration_threshold: str = dspy.OutputField(
        desc="Price point or capacity point at which a different kit becomes worth it."
    )


class DecideRAM(dspy.Module):
    # Telemetry metadata — bump signature_version only when this signature's
    # input/output fields change shape (GEPA needs a consistent field shape).
    signature_name = "DecideRAM"
    # v2: chooses a RAM group (spec), not an exact kit name; the branded kit is
    # resolved deterministically after.
    signature_version = 2
    category = "ram"
    output_name_field = "ram_group"

    def __init__(self) -> None:
        self.chain = dspy.ChainOfThought(RAMSelection)

    def forward(self, use_cases, ddr_gen, budget_ceiling, candidates):
        return self.chain(
            use_cases=use_cases,
            ddr_gen=ddr_gen,
            budget_ceiling=budget_ceiling,
            candidates=candidates,
        )


@lru_cache(maxsize=1)
def load_program() -> DecideRAM:
    module = DecideRAM()
    if WEIGHTS_PATH.exists():
        module.load(str(WEIGHTS_PATH))
    return module


def optimize(trainset, metric, num_iterations=10, save=True) -> DecideRAM:
    module = DecideRAM()
    optimizer = dspy.GEPA(metric=metric, num_iterations=num_iterations)
    optimized = optimizer.compile(module, trainset=trainset)
    if save:
        WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        optimized.save(str(WEIGHTS_PATH))
    return optimized
