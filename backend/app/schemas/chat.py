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