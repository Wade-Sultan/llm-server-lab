from __future__ import annotations

from functools import lru_cache

import dspy


class PSUSelection(dspy.Signature):
    """
    Select the best PSU *group* (spec) for this build from the candidates.

    Each candidate is a PSU spec (wattage / efficiency / form factor), not a
    specific product — the exact branded unit is resolved deterministically
    afterwards. Choose at the spec level here. All candidates already meet the
    minimum wattage and form factor requirements. Choose the best
    efficiency/price/headroom combination. Prefer Gold or better efficiency
    unless budget is very tight. A 20% wattage headroom over the system TDP is a
    reasonable minimum.

    Wattage is not the only constraint on a multi-GPU or workstation build: the
    unit also has to physically reach every load. Each GPU needs its own
    8-pin/12-pin/16-pin run, and high-TDP workstation boards take two EPS
    connectors rather than one. A supply with the headroom but not the
    connectors is the wrong pick.
    """

    required_wattage: int = dspy.InputField(
        desc="Minimum wattage required by the system in watts"
    )
    budget_ceiling: int = dspy.InputField(
        desc="Maximum to spend on PSU in USD; -1 means no ceiling — the user has said cost is not a constraint"
    )
    candidates: str = dspy.InputField(
        desc="JSON list of PSU groups with the group's street price. Fields: "
        "psu_group, wattage, efficiency, form_factor, modular, pcie_8pin, "
        "pcie_12pin, pcie_16pin, eps_connectors, street_price_usd"
    )

    psu_group: str = dspy.OutputField(
        desc="Exact psu_group label of the chosen spec, matching a candidate"
    )
    reason: str = dspy.OutputField(desc="One sentence justification.")


class DecidePSU(dspy.Module):
    # Telemetry metadata — bump signature_version only when this signature's
    # input/output fields change shape (GEPA needs a consistent field shape).
    signature_name = "DecidePSU"
    # v2: chooses a PSU group (spec), not an exact unit name; the branded unit is
    # resolved deterministically after.
    signature_version = 2
    category = "psu"
    output_name_field = "psu_group"

    def __init__(self) -> None:
        # Plain Predict — no chain-of-thought needed for this slot
        self.predict = dspy.Predict(PSUSelection)

    def forward(self, required_wattage, budget_ceiling, candidates):
        return self.predict(
            required_wattage=required_wattage,
            budget_ceiling=budget_ceiling,
            candidates=candidates,
        )


@lru_cache(maxsize=1)
def load_program() -> DecidePSU:
    # No saved weights — PSU selection doesn't benefit from GEPA optimization
    return DecidePSU()
