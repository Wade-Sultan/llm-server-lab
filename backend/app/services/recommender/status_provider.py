"""
status_provider.py
==================
DSPy StatusMessageProvider that emits user-facing progress messages for each
component-selection module and for LM calls.

Registered once per pipeline run via dspy.streamify().  Because it has no
mutable state it can be a module-level singleton — streamify creates a fresh
MemoryObjectSendStream per call, so concurrent requests stay isolated.
"""

from __future__ import annotations

import json
from typing import Any

from dspy.streaming.messages import StatusMessageProvider

_COMPONENT_LABELS: dict[str, str] = {
    "DecideDDR": "DDR generation",
    "DecideCPU": "CPU",
    "DecideCPUCooler": "cooler",
    "DecideMotherboard": "motherboard",
    "DecideRAM": "RAM",
    "DecideStorage": "storage",
    "DecideGPU": "GPU",
    "DecidePSU": "power supply",
    "DecideCase": "case",
    "DecideFans": "fan",
}


class BuildStatusProvider(StatusMessageProvider):
    """
    Emits two levels of status messages:

    1. module_start — fires when a Decide* module enters forward(), after the
       candidate list has been assembled.  Includes a candidate count when the
       candidates field is a JSON array.

    2. lm_start — fires just before the LLM call, so the user knows the AI
       portion is running (the slow part).

    Inner modules (ChainOfThought, Predict) return None and are silently
    skipped by StatusStreamingCallback.
    """

    def module_start_status_message(self, instance: Any, inputs: dict[str, Any]) -> str | None:
        label = _COMPONENT_LABELS.get(type(instance).__name__)
        if not label:
            return None

        candidates_raw = inputs.get("candidates")
        if candidates_raw:
            try:
                count = len(json.loads(candidates_raw))
                plural = "s" if count != 1 else ""
                return f"Evaluating {count} {label} option{plural}…"
            except (json.JSONDecodeError, TypeError):
                pass

        return f"Evaluating {label} options…"

    def lm_start_status_message(self, instance: Any, inputs: dict[str, Any]) -> str | None:
        return "Consulting AI…"

    def module_end_status_message(self, outputs: Any) -> str | None:
        return None

    def lm_end_status_message(self, outputs: Any) -> str | None:
        return None
