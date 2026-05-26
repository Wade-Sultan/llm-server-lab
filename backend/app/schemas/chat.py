import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field
from typing import Any, Callable, Literal

class ChatMessage(BaseModel):
    role: str # User or Assistant
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

class ConversationDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None
    created_at: datetime
    messages: list[MessageOut]

class BuildProfile(BaseModel):
    primary_use: str        # "gaming" | "video_editing" | "local_llm" | "general"
    gaming_resolution: str | None = None  # "1080p" | "1440p" | "4k"
    budget_tier: str        # "entry" | "mid" | "high" | "elite"
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
    wifi_required: bool = True
    color_theme: str | None = Field(None, description="e.g. 'black', 'white', 'black & red'")


class BuildRequest(BaseModel):
    """
    Structured input sent from the frontend Build Configurator.

    `answers` is a flat dict keyed by "<useCase>.<questionId>" whose values
    are either a single string (single-select) or a list of strings
    (multi-select), mirroring the React state.
    """
    use_cases: list[str] = Field(
        ...,
        description="Selected use-case keys: gaming, productivity, creative, streaming, aiml, nas",
    )
    budget_usd: int = Field(..., description="Total build budget in USD")
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
        parts = [self.cpu, self.cpu_cooler, self.motherboard, self.ram,
                 self.storage, self.case, self.psu]
        optional = [p for p in [self.gpu, self.fans] if p is not None]
        all_parts = parts + optional
        if any(p.price_usd is None for p in all_parts):
            return None
        self.total_price_usd = round(sum(p.price_usd for p in all_parts), 2)  # type: ignore[arg-type]
        return self.total_price_usd