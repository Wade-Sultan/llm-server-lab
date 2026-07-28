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
    """
 
    use_cases: str = dspy.InputField(desc="User's use cases and preferences summary")
    cpu_name: str = dspy.InputField(desc="Chosen CPU — drives VRM and chipset needs")
    ddr_gen: str = dspy.InputField(desc="Required DDR generation (ddr4 or ddr5)")
    budget_ceiling: int = dspy.InputField(desc="Maximum to spend on motherboard in USD")
    candidates: str = dspy.InputField(
        desc="JSON list of compatible motherboards. Fields: name, socket, chipset, "
             "form_factor, ddr_gen, ram_slots, m2_slots, sata_ports, has_wifi, "
             "supports_bios_flashback, vrm_quality, street_price_usd"
    )
 
    motherboard_name: str = dspy.OutputField(desc="Exact product name of the chosen motherboard")
    reason: str = dspy.OutputField(desc="1-2 sentences focused on why this chipset/VRM tier fits.")
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