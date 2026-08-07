from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import dspy

WEIGHTS_PATH = Path(__file__).parent / "weights" / "decidemotherboard.json"


class MotherboardSelection(dspy.Signature):
    """
    Select the best motherboard for this build from the given candidates.

    All candidates already match the CPU socket, DDR generation, form factor,
    and WiFi requirement. Focus on value: don't recommend premium VRMs or
    high-end chipsets for builds that won't benefit from them.

    For server and workstation builds the board is a capacity decision rather
    than a tier decision. ram_slots and memory_channels together decide whether
    the memory subsystem can be populated at full bandwidth, and supports_ecc /
    has_ipmi are hard requirements for anything expected to run unattended.
    Spending up for those on a server build is correct; spending up for them on
    a desktop is the same mistake as a premium VRM nobody uses.

    pcie_x16_slots deserves particular weight because it is decided here and
    cannot be revisited: the GPU step runs after this one and is capped by
    whatever this board provides, so a single-slot board silently forecloses a
    multi-GPU build no matter what the user asked for. When the use case points
    at hosting several GPUs — an LLM server, multi-GPU training or inference,
    a render node — prefer a board with more than one x16 slot, and be willing
    to pay a premium for it that you would not pay on a desktop.

    This is a preference, not a rule. A single-slot board that is the better
    board on every other axis is still the right pick when nothing in the use
    case calls for a second card, and for ordinary desktop builds extra x16
    slots are worth nothing — do not spend on capacity the workload will never
    use.
    """

    use_cases: str = dspy.InputField(desc="User's use cases and preferences summary")
    cpu_name: str = dspy.InputField(desc="Chosen CPU — drives VRM and chipset needs")
    # Add ddr6 to the desc when DDR6 parts exist.
    ddr_gen: str = dspy.InputField(desc="Required DDR generation (ddr4 or ddr5)")
    budget_ceiling: int = dspy.InputField(
        desc="Maximum to spend on motherboard in USD; -1 means no ceiling — the user has said cost is not a constraint"
    )
    candidates: str = dspy.InputField(
        desc="JSON list of compatible motherboards. Fields: name, socket, chipset, "
        "form_factor, ddr_gen, ram_slots, m2_slots, sata_ports, has_wifi, "
        "pcie_x16_slots, memory_channels, supports_ecc, has_ipmi, "
        "street_price_usd"
    )

    motherboard_name: str = dspy.OutputField(
        desc="Exact product name of the chosen motherboard"
    )
    reason: str = dspy.OutputField(
        desc="1-2 sentences focused on why this chipset/VRM tier fits."
    )
    reconsideration_threshold: str = dspy.OutputField(
        desc="Price point at which a step-up board meaningfully improves this build."
    )


class DecideMotherboard(dspy.Module):
    # Telemetry metadata — bump signature_version only when this signature's
    # input/output fields change shape (GEPA needs a consistent field shape).
    signature_name = "DecideMotherboard"
    signature_version = 1
    category = "motherboard"
    output_name_field = "motherboard_name"

    def __init__(self) -> None:
        self.chain = dspy.ChainOfThought(MotherboardSelection)

    def forward(self, use_cases, cpu_name, ddr_gen, budget_ceiling, candidates):
        return self.chain(
            use_cases=use_cases,
            cpu_name=cpu_name,
            ddr_gen=ddr_gen,
            budget_ceiling=budget_ceiling,
            candidates=candidates,
        )


@lru_cache(maxsize=1)
def load_program() -> DecideMotherboard:
    module = DecideMotherboard()
    if WEIGHTS_PATH.exists():
        module.load(str(WEIGHTS_PATH))
    return module


def optimize(trainset, metric, num_iterations=10, save=True) -> DecideMotherboard:
    module = DecideMotherboard()
    optimizer = dspy.GEPA(metric=metric, num_iterations=num_iterations)
    optimized = optimizer.compile(module, trainset=trainset)
    if save:
        WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        optimized.save(str(WEIGHTS_PATH))
    return optimized
