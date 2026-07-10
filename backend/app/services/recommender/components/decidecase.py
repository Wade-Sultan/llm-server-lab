from __future__ import annotations
 
import dspy
 
 
class CaseOptions(dspy.Signature):
    """
    Select 3 case options for the user to choose from.
 
    All candidates already fit the motherboard form factor and GPU clearance.
    Return exactly 3 options representing different size/aesthetic/price points
    that all work with this build. Rank them: best value first, then alternatives.
    """
 
    use_cases: str = dspy.InputField(desc="User's use cases and preferences, including color theme")
    mobo_form_factor: str = dspy.InputField(desc="Required motherboard form factor support")
    budget_ceiling: int = dspy.InputField(desc="Maximum to spend on case in USD")
    candidates: str = dspy.InputField(
        desc="JSON list of compatible cases. Fields: name, size, supported_mobo_sizes, "
             "max_gpu_length_mm, max_cooler_height_mm, fan_slots, included_fans, "
             "street_price_usd"
    )
 
    option_1: str = dspy.OutputField(desc="Best value pick — exact product name")
    option_1_reason: str = dspy.OutputField(desc="One sentence.")
    option_2: str = dspy.OutputField(desc="Alternative pick — exact product name")
    option_2_reason: str = dspy.OutputField(desc="One sentence.")
    option_3: str = dspy.OutputField(desc="Third option (different size or style) — exact product name")
    option_3_reason: str = dspy.OutputField(desc="One sentence.")
 
 
class DecideCase(dspy.Module):
    # Telemetry metadata — bump signature_version only when this signature's
    # input/output fields change shape (GEPA needs a consistent field shape).
    # output_name_field is the primary (best-value) option; user picks later.
    signature_name = "DecideCase"
    signature_version = 1
    category = "case"
    output_name_field = "option_1"

    def __init__(self) -> None:
        self.chain = dspy.ChainOfThought(CaseOptions)
 
    def forward(self, use_cases, mobo_form_factor, budget_ceiling, candidates):
        return self.chain(
            use_cases=use_cases,
            mobo_form_factor=mobo_form_factor,
            budget_ceiling=budget_ceiling,
            candidates=candidates,
        )
 
 
def load_program() -> DecideCase:
    return DecideCase()