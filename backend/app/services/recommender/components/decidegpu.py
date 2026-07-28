from __future__ import annotations
 
from functools import lru_cache
from pathlib import Path
 
import dspy
 
WEIGHTS_PATH = Path(__file__).parent / "weights" / "decidegpu.json"
 
 
class GPUSelection(dspy.Signature):
    """
    Select the best GPU *chipset* for this build from the given candidates.

    Each candidate is a chipset (e.g. "RTX 5080"), not a specific board — the
    exact board variant (brand, length, price) is resolved deterministically
    later, once the case and PSU are known. Choose at the chipset level here.

    This is the most financially consequential component choice. Consider:
    - Whether the target resolution and use case justify the VRAM tier
    - Whether a last-gen chipset at lower cost outperforms a current-gen at this price
    - Whether ray tracing is a meaningful factor given the user's playstyle
    - If used_market_viable is true for a candidate, note it as a potential
      cost-saving option the user could consider on eBay

    Output a reconsideration_threshold that is specific about price and tier.
    """

    use_cases: str = dspy.InputField(desc="User's use cases, target resolution, and preferences")
    budget_total: int = dspy.InputField(desc="Total build budget in USD")
    gpu_budget_ceiling: int = dspy.InputField(desc="Maximum to spend on GPU in USD")
    candidates: str = dspy.InputField(
        desc="JSON list of GPU chipsets with the chipset's street price. Fields: "
             "chipset, brand, vram_gb, tdp_w, street_price_usd, used_market_viable"
    )

    gpu_chipset: str = dspy.OutputField(
        desc="Exact chipset string of the chosen GPU, matching a chipset from candidates"
    )
    reason: str = dspy.OutputField(
        desc="2-3 sentences. Lead with the value argument relative to the use case. "
             "Mention used market if relevant."
    )
    reconsideration_threshold: str = dspy.OutputField(
        desc="Specific price boundary at which a tier upgrade or downgrade becomes "
             "worth reconsidering. Include the alternative chipset and dollar figure."
    )
    gpu_required: bool = dspy.OutputField(
        desc="False only if the use case is fully covered by integrated graphics "
             "and no discrete GPU is needed."
    )
 
 
class DecideGPU(dspy.Module):
    # Telemetry metadata — bump signature_version only when this signature's
    # input/output fields change shape (GEPA needs a consistent field shape).
    signature_name = "DecideGPU"
    # v2: main step chooses a chipset (gpu_chipset), not an exact board name —
    # the specific board is resolved deterministically after case + PSU.
    signature_version = 2
    category = "gpu"
    output_name_field = "gpu_chipset"

    def __init__(self) -> None:
        self.chain = dspy.ChainOfThought(GPUSelection)
 
    def forward(self, use_cases, budget_total, gpu_budget_ceiling, candidates):
        return self.chain(
            use_cases=use_cases,
            budget_total=budget_total,
            gpu_budget_ceiling=gpu_budget_ceiling,
            candidates=candidates,
        )
 
 
@lru_cache(maxsize=1)
def load_program() -> DecideGPU:
    module = DecideGPU()
    if WEIGHTS_PATH.exists():
        module.load(str(WEIGHTS_PATH))
    return module
 
 
def optimize(trainset, metric, num_iterations=10, save=True) -> DecideGPU:
    module = DecideGPU()
    optimizer = dspy.GEPA(metric=metric, num_iterations=num_iterations)
    optimized = optimizer.compile(module, trainset=trainset)
    if save:
        WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        optimized.save(str(WEIGHTS_PATH))
    return optimized