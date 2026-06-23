from __future__ import annotations

from pathlib import Path

import dspy

WEIGHTS_PATH = Path(__file__).parent / "weights" / "extractprofile.json"


class ProfileExtraction(dspy.Signature):
    """
    Extract a structured intent profile from a PC build consultation conversation.

    Infer the user's actual needs rather than echoing their words literally.
    Pick the most demanding use case when multiple are mentioned.
    Infer budget tier from context clues even when no dollar figure is given.
    Output 'none' for gaming_resolution when primary_use is not gaming or resolution is unknown.
    Output 'unknown' for primary_use or budget_tier ONLY when the conversation truly gives no
    basis to infer them yet — do not guess just to avoid 'unknown'.
    """

    conversation: str = dspy.InputField(
        desc="Full conversation history, each turn prefixed with 'User:' or 'Assistant:'"
    )

    primary_use: str = dspy.OutputField(
        desc="Exactly one of: gaming, video_editing, local_llm, general, unknown — "
             "'unknown' if the conversation gives no basis to infer a use case yet"
    )
    gaming_resolution: str = dspy.OutputField(
        desc="Exactly one of: 1080p, 1440p, 4k, none"
    )
    budget_tier: str = dspy.OutputField(
        desc="Exactly one of: entry, mid, high, elite, unknown — "
             "entry ≈$1000–1500, mid ≈$1500–2300, high ≈$2300–3500, elite ≈$3500+ — "
             "'unknown' if no budget signal at all has been given"
    )
    games: str = dspy.OutputField(
        desc="Comma-separated game titles the user mentioned, or empty string if none"
    )
    workloads: str = dspy.OutputField(
        desc="Comma-separated workload descriptions the user mentioned, or empty string if none"
    )
    notes: str = dspy.OutputField(
        desc="Any remaining constraints or preferences not captured above, or empty string"
    )


class ExtractProfile(dspy.Module):
    def __init__(self) -> None:
        self.predict = dspy.ChainOfThought(ProfileExtraction)

    def forward(self, conversation: str) -> dspy.Prediction:
        return self.predict(conversation=conversation)


def load_program() -> ExtractProfile:
    """Load saved weights if available, otherwise return a fresh module."""
    module = ExtractProfile()
    if WEIGHTS_PATH.exists():
        module.load(str(WEIGHTS_PATH))
    return module


def optimize(
    trainset: list[dspy.Example],
    metric,
    num_iterations: int = 10,
    save: bool = True,
) -> ExtractProfile:
    """
    Run GEPA to optimize the profile extraction prompt.

    Each Example in trainset should have:
        - conversation (str)      ← formatted "User: ...\nAssistant: ..." history
        - primary_use (str)       ← gold label
        - gaming_resolution (str) ← gold label, 'none' if N/A
        - budget_tier (str)       ← gold label
        - games (str)             ← comma-separated, or empty string
        - workloads (str)         ← comma-separated, or empty string
        - notes (str)

    Metric receives (example, prediction, trace=None) and returns float in [0, 1].
    Weight primary_use and budget_tier heavily — they drive all downstream routing.
    """
    module = ExtractProfile()
    optimizer = dspy.GEPA(metric=metric, num_iterations=num_iterations)
    optimized = optimizer.compile(module, trainset=trainset)

    if save:
        WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        optimized.save(str(WEIGHTS_PATH))

    return optimized
