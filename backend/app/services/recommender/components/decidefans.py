from __future__ import annotations

from functools import lru_cache

import dspy


class FanSelection(dspy.Signature):
    """
    Select additional case fans for this build, if needed.

    If included case fans are sufficient for this build's thermal requirements,
    output fan_name as 'NONE'. Otherwise pick the best value option and say how
    many of it to buy.

    fan_quantity is a count of the chosen product as it is sold, which is not
    the same as a count of fans: candidates carry pack_count, so a 3-pack
    filling three empty slots is fan_quantity 1, not 3. Never order more than
    empty_fan_slots worth of fans — airflow the case cannot mount is money
    spent on nothing.

    Size the count to the heat actually being removed. A build with several
    high-TDP GPUs is dumping hundreds of watts into the case and wants every
    slot populated; a low-power desktop is usually fine on the included fans.
    """

    cpu_tdp_w: int = dspy.InputField(desc="CPU TDP in watts")
    gpu_tdp_w: int = dspy.InputField(
        desc="Total GPU TDP in watts across every card in the build, 0 if no "
        "discrete GPU"
    )
    case_included_fans: int = dspy.InputField(
        desc="Number of fans included with the case"
    )
    empty_fan_slots: int = dspy.InputField(
        desc="Mounting points left after the included fans — the hard ceiling on "
        "how many additional fans can be fitted"
    )
    budget_ceiling: int = dspy.InputField(
        desc="Maximum to spend on additional fans in USD; -1 means no ceiling — the user has said cost is not a constraint"
    )
    candidates: str = dspy.InputField(
        desc="JSON list of compatible fans. Fields: name, size_mm, airflow_cfm, "
        "noise_db, pack_count, street_price_usd"
    )

    fan_name: str = dspy.OutputField(
        desc="Exact product name, or 'NONE' if included fans are sufficient"
    )
    fan_quantity: int = dspy.OutputField(
        desc="How many of that product to buy, at least 1 and never more than "
        "empty_fan_slots. 1 when fan_name is 'NONE'."
    )
    reason: str = dspy.OutputField(desc="One sentence.")


class DecideFans(dspy.Module):
    # Telemetry metadata — bump signature_version only when this signature's
    # input/output fields change shape (GEPA needs a consistent field shape).
    signature_name = "DecideFans"
    # v2: adds fan_quantity and the empty_fan_slots ceiling it is bounded by,
    # so a build can order more than one of the chosen fan.
    signature_version = 2
    category = "fans"
    output_name_field = "fan_name"

    def __init__(self) -> None:
        self.predict = dspy.Predict(FanSelection)

    def forward(
        self,
        cpu_tdp_w,
        gpu_tdp_w,
        case_included_fans,
        empty_fan_slots,
        budget_ceiling,
        candidates,
    ):
        return self.predict(
            cpu_tdp_w=cpu_tdp_w,
            gpu_tdp_w=gpu_tdp_w,
            case_included_fans=case_included_fans,
            empty_fan_slots=empty_fan_slots,
            budget_ceiling=budget_ceiling,
            candidates=candidates,
        )


@lru_cache(maxsize=1)
def load_program() -> DecideFans:
    return DecideFans()
