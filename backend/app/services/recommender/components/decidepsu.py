from __future__ import annotations
 
import dspy
 
 
class PSUSelection(dspy.Signature):
    """
    Select the best PSU for this build from the given candidates.
 
    All candidates already meet the minimum wattage and form factor requirements.
    Choose the best efficiency/price/headroom combination. Prefer Gold or better
    efficiency unless budget is very tight. A 20% wattage headroom over the
    system TDP is a reasonable minimum.
    """
 
    required_wattage: int = dspy.InputField(desc="Minimum wattage required by the system in watts")
    budget_ceiling: int = dspy.InputField(desc="Maximum to spend on PSU in USD")
    candidates: str = dspy.InputField(
        desc="JSON list of compatible PSUs. Fields: name, wattage, efficiency, "
             "modular, form_factor, street_price_usd"
    )
 
    psu_name: str = dspy.OutputField(desc="Exact product name of the chosen PSU")
    reason: str = dspy.OutputField(desc="One sentence justification.")
 
 
class DecidePSU(dspy.Module):
    # Telemetry metadata — bump signature_version only when this signature's
    # input/output fields change shape (GEPA needs a consistent field shape).
    signature_name = "DecidePSU"
    signature_version = 1
    category = "psu"
    output_name_field = "psu_name"

    def __init__(self) -> None:
        # Plain Predict — no chain-of-thought needed for this slot
        self.predict = dspy.Predict(PSUSelection)
 
    def forward(self, required_wattage, budget_ceiling, candidates):
        return self.predict(
            required_wattage=required_wattage,
            budget_ceiling=budget_ceiling,
            candidates=candidates,
        )
 
 
def load_program() -> DecidePSU:
    # No saved weights — PSU selection doesn't benefit from GEPA optimization
    return DecidePSU()