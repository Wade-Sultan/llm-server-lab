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

    A build may take more than one drive, so storage_groups is a list. One
    drive is the right answer for most desktops and should stay the default —
    a second drive is only worth its money when the build genuinely wants two
    different kinds of storage, not merely more of the same. Prefer a single
    larger drive over two smaller ones at equal capacity and price.

    The case for splitting is a mixed workload: a fast NVMe for the OS and
    working set plus bulk capacity for things that are read sequentially and
    rarely — model weights, datasets, footage, media libraries. An LLM server
    is the clearest example, where weights run to hundreds of gigabytes but see
    nothing like the random-access pressure the OS drive does, and paying NVMe
    prices for that capacity is waste.

    Never exceed max_drives; those are the physical mounting points the chosen
    motherboard actually has.
    """

    use_cases: str = dspy.InputField(desc="User's use cases and preferences summary")
    budget_ceiling: int = dspy.InputField(
        desc="Maximum to spend on storage in USD; -1 means no ceiling — the user has said cost is not a constraint"
    )
    max_drives: int = dspy.InputField(
        desc="Most drives this build can physically mount (M.2 slots + SATA "
        "ports on the chosen board). A hard ceiling on how many groups to name."
    )
    candidates: str = dspy.InputField(
        desc="JSON list of storage groups with the group's street price. Fields: "
        "storage_group, type, interface, capacity_gb, seq_read_mbs, "
        "seq_write_mbs, street_price_usd"
    )

    storage_groups: str = dspy.OutputField(
        desc="Chosen storage_group labels, comma-separated, each matching a "
        "candidate exactly. Usually one. Order them primary drive first. Do "
        "not name the same group twice."
    )
    reason: str = dspy.OutputField(
        desc="1-2 sentences. Address whether the interface tier is justified, "
        "and if naming more than one drive, what each is for."
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
    # v3: storage_group -> storage_groups (a list), plus the max_drives input
    # bounding it, so a build can pair a fast OS drive with bulk capacity.
    signature_version = 3
    category = "storage"
    output_name_field = "storage_groups"

    def __init__(self) -> None:
        self.chain = dspy.ChainOfThought(StorageSelection)

    def forward(self, use_cases, budget_ceiling, max_drives, candidates):
        return self.chain(
            use_cases=use_cases,
            budget_ceiling=budget_ceiling,
            max_drives=max_drives,
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
