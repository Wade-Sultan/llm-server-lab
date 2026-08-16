import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Sentinel budget ceiling meaning "no ceiling at all" — the 'custom' budget
# tier, where the user has explicitly said cost is not a constraint.
#
# A sentinel rather than a very large number because those are not the same
# claim: a $1,000,000 ceiling still filters the candidate query and still
# reaches the LLM as a figure to reason against, and "spend up to a million"
# is advice nobody asked for. Negative so it can never be mistaken for a real
# budget, and so an unguarded `price <= ceiling` comparison that forgot to
# check for it fails loudly (empty candidate set) rather than silently.
NO_BUDGET_CEILING = -1


class ChatMessage(BaseModel):
    role: str  # User or Assistant
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    conversation_id: str | None = None


class ConversationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime
    message_count: int


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: str
    content: str | None
    created_at: datetime
    metadata: dict | None = None


class FeedbackIn(BaseModel):
    """A thumbs up/down on the build a conversation recommended.

    build_key rather than a pc_builds id: the client only ever sees the key (it
    is what rides on the build message part), and resolving it server-side is
    also what stops a client naming an arbitrary build row.
    """

    rating: Literal["up", "down"]
    build_key: str | None = None


class FeedbackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    rating: Literal["up", "down"]
    build_id: uuid.UUID | None


class ConversationDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None
    created_at: datetime
    messages: list[MessageOut]
    # None when this user has not rated the conversation. Carried on the detail
    # payload so a reloaded thread shows the thumb already lit, rather than
    # needing a second request per conversation.
    feedback: FeedbackOut | None = None


class BuildProfile(BaseModel):
    # "gaming" | "streaming" | "video_editing" | "3d_rendering" | "ai"
    # | "server" | "software_dev" | "music_production" | "general"
    primary_use: str
    gaming_resolution: str | None = None  # "1080p" | "1440p" | "4k"
    gaming_fps: str | None = None  # "60" | "120" | "144" | "240"
    streaming_style: str | None = None  # "while_gaming" | "camera_only"
    ai_workload: str | None = None  # "inference" | "training" | "image_gen"
    ai_model_scale: str | None = None  # "small" | "medium" | "large"
    # --- LLM serving shape ----------------------------------------------------
    # Only meaningful for an LLM build (see chat_pipeline._is_llm_build). These
    # two are the dominant terms in how much VRAM the machine actually needs,
    # and leaving them unstated is what let a 31B model be sized as though it
    # were served at fp16 — a $15000 card for a job a $800 one does.
    #
    # "yes" (quantization is fine) | "no" (full precision) | "unsure".
    # 'unsure' is a real answer, not an absence: it resolves to the q4-q8
    # default that self-hosters actually run, and it exists so a user who has
    # never heard of quantization is not blocked by the question.
    llm_quantization: str | None = None
    # "4k" | "8k" | "32k" | "128k" | "unsure" — target context window. Drives
    # KV cache, which at long context rivals the weights themselves.
    llm_context_tokens: str | None = None
    # "ai_training" | "ai_serving" | "hpc" | "virtualization" | "storage"
    # | "render_farm"
    server_workload: str | None = None
    # "0" | "1" | "2" | "4" | "8" — kept a string, like gaming_fps: it is a
    # bucket label the extraction model emits, not an arithmetic quantity.
    server_gpu_count: str | None = None
    editing_resolution: str | None = None  # "1080p" | "4k" | "6k_plus"
    rendering_software: str | None = None  # free text, e.g. "Blender"
    workload_intensity: str | None = None  # "light" | "moderate" | "heavy"
    # "entry" | "mid" | "high" | "elite" | "custom". 'custom' means no ceiling
    # applies anywhere; it is never inferred, only accepted when the user says
    # so outright — see chat_pipeline._confirm_custom_budget.
    budget_tier: str
    # The dollar figure the user actually named for the whole build, when they
    # named one. A tier is a band and cannot round-trip a number: every tier
    # resolves to a single constant, so "$2200" and "$1500" both come back out
    # as $1500. This preserves the figure so _budget_for can spend it directly
    # and only fall back to the ladder when nothing was stated.
    stated_budget_usd: int | None = None
    # How firm the budget is, independent of how big it is: "firm" | "flexible"
    # | "stretch". Scales the budget figure in chat_pipeline._budget_for.
    price_sensitivity: str | None = None

    # --- Stated preferences -------------------------------------------------
    # Volunteered by the user, never inferred from the use case, and never
    # required to reach a build. They populate UserPreferences (and the Q&A
    # string) on the way into the DSPy pipeline, which already knows how to
    # read all of them — the chat path just never filled them in before.
    form_factor: str | None = None  # "atx" | "matx" | "itx" | "no_preference"
    color_theme: str | None = None  # free text, e.g. "black & white"
    rgb_lighting: str | None = None  # "yes" | "no"
    noise_tolerance: str | None = None  # "quiet" | "normal"

    games: list[str] = []
    workloads: list[str] = []
    notes: str = ""


class ChatResponse(BaseModel):
    reply: str
    ready: bool
    build: dict[str, Any] | None = None  # full build object when ready
    build_key: str | None = None


class UserPreferences(BaseModel):
    """Optional high-level preferences that sit outside the per-use-case Q&A."""

    preferred_brand_cpu: Literal["amd", "intel", "no_preference"] = "no_preference"
    preferred_brand_gpu: Literal["nvidia", "amd", "no_preference"] = "no_preference"
    form_factor: Literal["atx", "matx", "itx", "no_preference"] = "no_preference"
    rgb_lighting: bool = False
    wifi_required: bool = False
    color_theme: str | None = Field(
        None, description="e.g. 'black', 'white', 'black & red'"
    )


class BuildRequest(BaseModel):
    """
    Structured input sent from the frontend Build Configurator.

    `answers` is a flat dict keyed by "<useCase>.<questionId>" whose values
    are either a single string (single-select) or a list of strings
    (multi-select), mirroring the React state.
    """

    use_cases: list[str] = Field(
        ...,
        description="Selected use-case keys: gaming, streaming, creator, rendering, "
        "aiml, server, dev, audio, productivity, nas",
    )
    budget_usd: int = Field(
        ...,
        description="Total build budget in USD, or NO_BUDGET_CEILING when the "
        "user has explicitly said cost is not a constraint",
    )
    preferences: UserPreferences = Field(default_factory=UserPreferences)
    answers: dict[str, str | list[str]] = Field(
        default_factory=dict,
        description="Flat map of '<useCase>.<questionId>' → answer(s)",
    )


class PartRecommendation(BaseModel):
    """A recommended component with pricing slots for downstream enrichment."""

    name: str = Field(..., description="Full product name")
    category: str = Field(..., description="Component category, e.g. 'CPU'")
    reason: str = Field(..., description="Why this part was chosen")
    price_usd: float | None = Field(None)
    amazon_url: str | None = Field(None)
    amazon_asin: str | None = Field(None)


class BuildRecommendation(BaseModel):
    """Complete build assembled by the pipeline."""

    cpu: PartRecommendation
    cpu_cooler: PartRecommendation
    motherboard: PartRecommendation
    ram: PartRecommendation
    storage: PartRecommendation
    gpu: PartRecommendation | None = None
    case: PartRecommendation
    psu: PartRecommendation
    fans: PartRecommendation | None = None
    build_notes: str = ""
    total_price_usd: float | None = None

    def compute_total_price(self) -> float | None:
        """Sum part prices. Returns None if any part is still unpriced."""
        parts = [
            self.cpu,
            self.cpu_cooler,
            self.motherboard,
            self.ram,
            self.storage,
            self.case,
            self.psu,
        ]
        optional = [p for p in [self.gpu, self.fans] if p is not None]
        all_parts = parts + optional
        if any(p.price_usd is None for p in all_parts):
            return None
        self.total_price_usd = round(sum(p.price_usd for p in all_parts), 2)  # type: ignore[arg-type]
        return self.total_price_usd
