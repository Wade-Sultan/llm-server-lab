from __future__ import annotations

import logging
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

from app.services.chat_models import ChatModelConfig
from app.services.chat_pipeline import _extra_body, _get_client, _usage_from_openai
from app.services.discovery.fetch import FetchedDoc

logger = logging.getLogger(__name__)

T = TypeVar("T")


class Sourced(BaseModel, Generic[T]):
    """A field value paired with the verbatim snippet that supports it.

    The wrapper (rather than a parallel provenance dict) lets the strict JSON
    schema force the model to justify every value. The model never emits a
    source URL — extraction is one call per source page, so the caller stamps
    the URL deterministically and provenance cannot be misattributed."""

    model_config = ConfigDict(extra="forbid")

    value: T | None  # None = "not stated on this page" — never guess
    snippet: str | None  # verbatim quote (<= 200 chars) supporting value


# ---------------------------------------------------------------------------
# Per-category schemas
# ---------------------------------------------------------------------------
# Field names mirror pc_parts + subtype columns exactly (msrp_usd is the one
# rename — unwrap() converts it to msrp_cents), so a staged item's
# extracted_fields dict is directly castable to the approval-time insert.


class CPUExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Sourced[str]  # canonical product name, e.g. "AMD Ryzen 7 9800X3D"
    manufacturer: Sourced[str]
    model_number: Sourced[str]
    year_released: Sourced[int]
    msrp_usd: Sourced[float]

    brand: Sourced[Literal["amd", "intel"]]
    socket: Sourced[str]
    tdp_watts: Sourced[int]
    has_igpu: Sourced[bool]
    ddr_generation: Sourced[list[Literal["ddr4", "ddr5"]]]
    supported_features: Sourced[list[str]]
    cores: Sourced[int]
    threads: Sourced[int]
    base_clock_ghz: Sourced[float]
    boost_clock_ghz: Sourced[float]
    l3_cache_mb: Sourced[int]
    pcie_generation: Sourced[int]
    max_memory_gb: Sourced[int]
    series: Sourced[str]


class GPUChipsetExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Sourced[str]  # chipset name, e.g. "RTX 5080" — not a board name
    vram_gb: Sourced[int]
    vram_type: Sourced[str]
    tdp_watts: Sourced[int]
    recommended_psu_watts: Sourced[int]
    pcie_generation: Sourced[int]
    base_clock_mhz: Sourced[int]
    boost_clock_mhz: Sourced[int]
    has_ray_tracing: Sourced[bool]
    cuda_cores: Sourced[int]
    tensor_cores: Sourced[int]
    stream_processors: Sourced[int]
    matrix_cores: Sourced[int]
    supported_features: Sourced[list[str]]


class GPUVariantExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Sourced[str]  # board product name, e.g. "MSI RTX 5080 Gaming Trio"
    manufacturer: Sourced[str]  # board partner, e.g. "msi"
    model_number: Sourced[str]
    year_released: Sourced[int]
    msrp_usd: Sourced[float]

    chipset_name: Sourced[str]  # resolved to gpu_chipset_id at approval
    brand: Sourced[Literal["nvidia", "amd", "intel"]]  # chip vendor
    length_mm: Sourced[int]
    width_slots: Sourced[float]
    pcie_power_pins: Sourced[str]
    display_outputs: Sourced[str]
    hdmi_version: Sourced[str]
    dp_version: Sourced[str]


class AIModelExtraction(BaseModel):
    """Schema only in v1 — the ai_model pipeline path is not wired yet. Mirrors
    ai_models columns; VRAM floors are computed, never extracted."""

    model_config = ConfigDict(extra="forbid")

    name: Sourced[str]
    family: Sourced[
        Literal[
            "llm", "multimodal", "image_gen", "video_gen", "speech",
            "audio_gen", "vision", "embedding", "classical", "rl",
        ]
    ]
    params_billions: Sourced[float]
    context_length: Sourced[int]
    developer: Sourced[str]
    license: Sourced[str]
    huggingface_id: Sourced[str]


CATEGORY_SCHEMAS: dict[str, type[BaseModel]] = {
    "cpu": CPUExtraction,
    "gpu_chipset": GPUChipsetExtraction,
    "gpu_variant": GPUVariantExtraction,
    "ai_model": AIModelExtraction,
}


def _response_format(model_cls: type[BaseModel]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": model_cls.__name__,
            "strict": True,
            "schema": model_cls.model_json_schema(),
        },
    }


_SYSTEM_PROMPT = """You extract PC hardware specifications from a single source page.

Rules:
- Extract ONLY what this page states about the requested product. Never guess,
  infer, or fill in from prior knowledge. If the page does not state a field,
  return {"value": null, "snippet": null} for it.
- Every non-null value must include a short verbatim snippet (max 200
  characters) copied from the page that supports it.
- Enum-like fields use lowercase vocabulary (e.g. "ddr5" never "DDR5",
  brands "amd"/"intel"/"nvidia").
- If the page covers multiple products, extract the one matching the request
  and null everything you cannot attribute to it specifically.
- msrp_usd is the launch/list price in US dollars."""


def _user_content(doc: FetchedDoc, target: str) -> str | list[dict[str, Any]]:
    header = f"Product to extract: {target}\nSource URL: {doc.url}"
    if doc.kind == "markdown":
        return f"{header}\n\nPage content (markdown):\n\n{doc.text}"
    parts: list[dict[str, Any]] = [
        {"type": "text", "text": f"{header}\n\nThe source is a PDF spec sheet, rasterized below."}
    ]
    parts.extend(
        {"type": "image_url", "image_url": {"url": u}} for u in (doc.images or [])
    )
    return parts


async def extract_from_source(
    doc: FetchedDoc,
    category: str,
    target: str,
    session_id: str | None,
    usage_events: list[dict],
) -> BaseModel | None:
    """One structured-extraction call for one source page. Appends each call's
    usage to usage_events. Returns None if the model can't produce a valid
    payload after one retry — the source is skipped, not fatal."""
    schema_cls = CATEGORY_SCHEMAS[category]
    client = _get_client()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _user_content(doc, target)},
    ]

    for attempt in range(2):
        resp = await client.chat.completions.create(
            model=ChatModelConfig.get_discovery_extract_model(),
            messages=messages,
            response_format=_response_format(schema_cls),
            temperature=0,
            **_extra_body(session_id),
        )
        usage_events.append(_usage_from_openai(resp.usage))
        raw = resp.choices[0].message.content or ""
        try:
            return schema_cls.model_validate_json(raw)
        except ValidationError as exc:
            logger.warning(
                "discovery: invalid extraction from %s (attempt %d): %s",
                doc.url, attempt + 1, exc,
            )
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Your JSON failed validation with these errors:\n"
                        f"{exc}\n\nReturn corrected JSON matching the schema exactly."
                    ),
                }
            )
    return None


def unwrap(extraction: BaseModel, source_url: str) -> tuple[dict, dict]:
    """Flatten a Sourced extraction into (values, provenance), dropping nulls
    and converting msrp_usd -> msrp_cents so keys match catalog columns."""
    values: dict[str, Any] = {}
    provenance: dict[str, dict] = {}
    for field, sourced in extraction:
        value = sourced.value
        if value is None:
            continue
        if field == "msrp_usd":
            field, value = "msrp_cents", round(value * 100)
        values[field] = value
        provenance[field] = {"source_url": source_url, "snippet": sourced.snippet}
    return values, provenance
