from __future__ import annotations
 
from functools import lru_cache
from pathlib import Path
 
import dspy
 
WEIGHTS_PATH = Path(__file__).parent / "weights" / "decidestorage.json"
 
 
class StorageSelection(dspy.Signature):
    """
    Select the best storage *group* (spec) for this build from the candidates.

    Each candidate is a storage spec (interface / capacity / speeds), not a
    specific product — the exact branded drive is resolved deterministically
    afterwards. Choose at the spec level here. Interface generation is a value
    question, not just a spec question: Gen5 costs more and runs hotter with
    minimal gaming benefit. Recommend based on workload, not on what's newest.
    Capacity is often more valuable than speed.
    """

    use_cases: str = dspy.InputField(desc="User's use cases and preferences summary")
    budget_ceiling: int = dspy.InputField(desc="Maximum to spend on storage in USD")
    candidates: str = dspy.InputField(
        desc="JSON list of storage groups with the group's street price. Fields: "
             "storage_group, type, interface, capacity_gb, seq_read_mbs, "
             "seq_write_mbs, street_price_usd"
    )

    storage_group: str = dspy.OutputField(desc="Exact storage_group label of the chosen spec, matching a candidate")
    reason: str = dspy.OutputField(
        desc="1-2 sentences. Address whether the interface tier is justified."
    )
    reconsideration_threshold: str = dspy.OutputField(
        desc="Capacity or price point at which a different option becomes worth it."
    )
 
 
class DecideStorage(dspy.Module):
    # Telemetry metadata — bump signature_version only when this signature's
    # input/output fields change shape (GEPA needs a consistent field shape).
    signature_name = "DecideStorage"
    # v2: chooses a storage group (spec), not an exact drive name; the branded
    # drive is resolved deterministically after.
    signature_version = 2
    category = "storage"
    output_name_field = "storage_group"

    def __init__(self) -> None:
        self.chain = dspy.ChainOfThought(StorageSelection)
 
    def forward(self, use_cases, budget_ceiling, candidates):
        return self.chain(
            use_cases=use_cases,
            budget_ceiling=budget_ceiling,
            candidates=candidates,
        )
 
 
@lru_cache(maxsize=1)
def load_program() -> DecideStorage:
    module = DecideStorage()
    if WEIGHTS_PATH.exists():
        module.load(str(WEIGHTS_PATH))
    return module
 
 
def optimize(trainset, metric, num_iterations=10, save=True) -> DecideStorage:
    module = DecideStorage()
    optimizer = dspy.GEPA(metric=metric, num_iterations=num_iterations)
    optimized = optimizer.compile(module, trainset=trainset)
    if save:
        WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        optimized.save(str(WEIGHTS_PATH))
    return optimized