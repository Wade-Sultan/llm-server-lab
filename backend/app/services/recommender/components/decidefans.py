from __future__ import annotations
 
import dspy
 
 
class FanSelection(dspy.Signature):
    """
    Select additional case fans for this build, if needed.
 
    If included case fans are sufficient for this build's thermal requirements,
    output fan_name as 'NONE'. Otherwise pick the best value option.
    """
 
    cpu_tdp_w: int = dspy.InputField(desc="CPU TDP in watts")
    gpu_tdp_w: int = dspy.InputField(desc="GPU TDP in watts, 0 if no discrete GPU")
    case_included_fans: int = dspy.InputField(desc="Number of fans included with the case")
    budget_ceiling: int = dspy.InputField(desc="Maximum to spend on additional fans in USD")
    candidates: str = dspy.InputField(
        desc="JSON list of compatible fans. Fields: name, size_mm, airflow_cfm, "
             "noise_db, pack_count, street_price_usd"
    )
 
    fan_name: str = dspy.OutputField(
        desc="Exact product name, or 'NONE' if included fans are sufficient"
    )
    reason: str = dspy.OutputField(desc="One sentence.")
 
 
class DecideFans(dspy.Module):
    # Telemetry metadata — bump signature_version only when this signature's
    # input/output fields change shape (GEPA needs a consistent field shape).
    signature_name = "DecideFans"
    signature_version = 1
    category = "fans"
    output_name_field = "fan_name"

    def __init__(self) -> None:
        self.predict = dspy.Predict(FanSelection)
 
    def forward(self, cpu_tdp_w, gpu_tdp_w, case_included_fans, budget_ceiling, candidates):
        return self.predict(
            cpu_tdp_w=cpu_tdp_w,
            gpu_tdp_w=gpu_tdp_w,
            case_included_fans=case_included_fans,
            budget_ceiling=budget_ceiling,
            candidates=candidates,
        )
 
 
def load_program() -> DecideFans:
    return DecideFans()