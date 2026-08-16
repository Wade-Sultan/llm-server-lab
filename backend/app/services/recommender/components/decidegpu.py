from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import dspy

from app.services.recommender.optimizing import run_gepa

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

    Also output gpu_count: how many of that chipset the build should use. Every
    card must be the same chipset — a build cannot mix models here, because the
    tensor/pipeline parallelism that makes multiple GPUs useful requires
    matched cards.

    For almost every desktop use case gpu_count is 1, and a second card is
    actively worse than one better card: games and creative applications do not
    scale across GPUs, so two mid cards lose to one strong card at the same
    money. Only raise it when total VRAM is the binding constraint and the
    workload genuinely shards across cards — serving or training a model too
    large for one card's memory is the case that matters. Two 24GB cards beat
    one 32GB card for a 40GB model and lose to it for everything else.

    max_gpu_slots is a hard ceiling from the chosen motherboard; never exceed
    it. cpu_pcie_lanes is advisory: past roughly 16 lanes per card the cards
    run at reduced width, which costs little for inference and a lot for
    training, so weigh it against the workload rather than treating it as a
    limit.

    WHEN THE BUILD IS FOR RUNNING AN LLM, VRAM IS A FUNCTION OF QUANTIZATION
    AND YOU MUST TREAT IT AS ONE. A model's weights shrink roughly in
    proportion to the bytes per weight it is served at:

        VRAM_GB ~= params_billions * bytes_per_weight * 1.2

    with bytes_per_weight of 2.0 for fp16/bf16, 1.0 for fp8/int8, 0.75 for q6,
    0.65 for q5, 0.5 for q4, 0.4 for q3 (the 1.2 covers KV cache and runtime
    overhead). A 31B model is ~74GB at fp16 and ~19GB at q4 — the first needs a
    workstation card costing five figures, the second runs on a 24GB consumer
    card. Picking the first when the user asked for the second is the single
    most expensive mistake available in this step.

    So: assume q4-q8 for anyone serving a model locally on one GPU, which is
    what self-hosters actually run, and choose the cheapest card that fits at a
    sane quantization. Size for fp16 only when the user asked for full
    precision, when the workload is training rather than inference, or when
    use_cases says so outright. State the quantization you assumed in `reason`
    — a VRAM claim without a precision attached is not a claim.

    This applies whether or not the model appears in our catalog. If use_cases
    names a model with no measured requirements, do the arithmetic above on the
    parameter count in its name rather than treating it as unconstrained.

    Output a reconsideration_threshold that is specific about price and tier.
    """

    use_cases: str = dspy.InputField(
        desc="User's use cases, target resolution, and preferences"
    )
    budget_total: int = dspy.InputField(
        desc="Total build budget in USD; -1 means the user has set no budget at all"
    )
    gpu_budget_ceiling: int = dspy.InputField(
        desc="Maximum to spend on GPU in USD; -1 means no ceiling — the user has said cost is not a constraint"
    )
    max_gpu_slots: int = dspy.InputField(
        desc="PCIe x16 slots on the chosen motherboard — the hard ceiling on "
        "gpu_count. 1 for most consumer boards."
    )
    cpu_pcie_lanes: int = dspy.InputField(
        desc="PCIe lanes the chosen CPU provides, or 0 if not recorded. "
        "Advisory context for how many cards can run at full width, not a limit."
    )
    candidates: str = dspy.InputField(
        desc="JSON list of GPU chipsets with the chipset's street price. Fields: "
        "chipset, brand, vram_gb, tdp_w, street_price_usd, used_market_viable"
    )

    gpu_chipset: str = dspy.OutputField(
        desc="Exact chipset string of the chosen GPU, matching a chipset from candidates"
    )
    gpu_count: int = dspy.OutputField(
        desc="How many of that chipset to use, at least 1 and never more than "
        "max_gpu_slots. Use 1 unless total VRAM across cards is the binding "
        "constraint for the workload."
    )
    reason: str = dspy.OutputField(
        desc="2-3 sentences. Lead with the value argument relative to the use case. "
        "Mention used market if relevant. For an LLM workload, state the "
        "quantization the VRAM figure assumes (e.g. 'fits Gemma 4 31B at q4 in "
        "~19GB') — the card only makes sense alongside the precision it was "
        "chosen for."
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
    # v3: adds gpu_count plus the max_gpu_slots / cpu_pcie_lanes inputs it is
    # decided against, so a build can host several matched cards.
    signature_version = 3
    category = "gpu"
    output_name_field = "gpu_chipset"

    def __init__(self) -> None:
        self.chain = dspy.ChainOfThought(GPUSelection)

    def forward(
        self,
        use_cases,
        budget_total,
        gpu_budget_ceiling,
        max_gpu_slots,
        cpu_pcie_lanes,
        candidates,
    ):
        return self.chain(
            use_cases=use_cases,
            budget_total=budget_total,
            gpu_budget_ceiling=gpu_budget_ceiling,
            max_gpu_slots=max_gpu_slots,
            cpu_pcie_lanes=cpu_pcie_lanes,
            candidates=candidates,
        )


@lru_cache(maxsize=1)
def load_program() -> DecideGPU:
    module = DecideGPU()
    if WEIGHTS_PATH.exists():
        module.load(str(WEIGHTS_PATH))
    return module


def optimize(
    trainset: list[dspy.Example],
    metric,
    save: bool = True,
    **gepa_kwargs,
) -> DecideGPU:
    return run_gepa(
        DecideGPU(), trainset, metric, WEIGHTS_PATH, save=save, **gepa_kwargs
    )
