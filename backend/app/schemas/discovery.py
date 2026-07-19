from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DiscoveryTriggerRequest(BaseModel):
    query: str = Field(min_length=3, max_length=200)
    category: Literal["cpu", "gpu_chipset", "gpu_variant"]


class DiscoveryTriggerResponse(BaseModel):
    run_id: uuid.UUID
    status: str


class DiscoveredItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    category: str
    name_normalized: str
    model_number: str | None
    extracted_fields: dict
    field_provenance: dict
    extraction_confidence: dict | None
    source_urls: list[str]
    matched_part_id: uuid.UUID | None
    matched_chipset_id: uuid.UUID | None
    match_method: str | None
    match_score: float | None
    validation_status: str
    validation_errors: list[dict] | None
    review_status: str
    created_at: datetime


class DiscoveryRunDetailOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_type: str
    status: str
    pipeline_version: str
    model_name: str
    sources_checked: int
    items_found: int
    items_new: int
    error_detail: str | None
    total_cost_usd: Decimal | None
    tokens_in: int | None
    tokens_out: int | None
    started_at: datetime
    finished_at: datetime | None
    items: list[DiscoveredItemOut]
