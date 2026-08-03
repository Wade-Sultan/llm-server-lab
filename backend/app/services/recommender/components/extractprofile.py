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
    Every use-case-specific field must be 'none' (or empty, for rendering_software)
    when it does not apply to the inferred primary_use or the conversation gives
    no basis to infer it yet.
    Output 'unknown' for primary_use or budget_tier ONLY when the conversation truly gives no
    basis to infer them yet — do not guess just to avoid 'unknown'.
    """

    conversation: str = dspy.InputField(
        desc="Full conversation history, each turn prefixed with 'User:' or 'Assistant:'"
    )

    primary_use: str = dspy.OutputField(
        desc="Exactly one of: gaming, streaming, video_editing, 3d_rendering, ai, "
        "software_dev, music_production, general, unknown — "
        "'unknown' if the conversation gives no basis to infer a use case yet"
    )
    gaming_resolution: str = dspy.OutputField(
        desc="Exactly one of: 1080p, 1440p, 4k, none — target gaming resolution; "
        "'none' unless primary_use is gaming or streaming"
    )
    gaming_fps: str = dspy.OutputField(
        desc="Exactly one of: 60, 120, 144, 240, none — target frame rate, inferred from "
        "monitor refresh rate or competitive-play cues when not stated outright; "
        "'none' unless primary_use is gaming or streaming"
    )
    streaming_style: str = dspy.OutputField(
        desc="Exactly one of: while_gaming, camera_only, none — whether the user streams "
        "gameplay or only camera/IRL/chatting content; 'none' unless primary_use is streaming"
    )
    ai_workload: str = dspy.OutputField(
        desc="Exactly one of: inference, training, image_gen, none — the dominant AI workload; "
        "'none' unless primary_use is ai"
    )
    ai_model_scale: str = dspy.OutputField(
        desc="Exactly one of: small, medium, large, none — LLM size the user wants to run: "
        "small ≈8B params or less, medium ≈934B, large ≈70B+; "
        "'none' unless primary_use is ai and the workload involves LLMs"
    )
    editing_resolution: str = dspy.OutputField(
        desc="Exactly one of: 1080p, 4k, 6k_plus, none — resolution of the footage the user "
        "edits; 'none' unless primary_use is video_editing"
    )
    rendering_software: str = dspy.OutputField(
        desc="The 3D software or renderer the user works in (e.g. Blender, V-Ray, Cinema 4D, "
        "Maya), or empty string if not mentioned; empty unless primary_use is 3d_rendering"
    )
    workload_intensity: str = dspy.OutputField(
        desc="Exactly one of: light, moderate, heavy, none — scale of the workload for "
        "software_dev (codebase size, VMs/containers) or music_production "
        "(track/plugin counts); 'none' for other use cases"
    )
    budget_tier: str = dspy.OutputField(
        desc="Exactly one of: entry, mid, high, elite, unknown — "
        "entry ≈$1000-1500, mid ≈$1500-2300, high ≈$2300-3500, elite ≈$3500+ — "
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
        - budget_tier (str)       ← gold label
        - gaming_resolution / gaming_fps / streaming_style / ai_workload /
          ai_model_scale / editing_resolution / workload_intensity (str)
                                  ← gold labels, 'none' if N/A
        - rendering_software (str) ← gold label, empty string if N/A
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
