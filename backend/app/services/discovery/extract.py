from __future__ import annotations

import logging
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

from app.services.chat_models import ChatModelConfig
from app.services.chat_pipeline import _extra_body, _get_client, _usage_from_openai
from app.services.discovery.fetch import FetchedDoc

logger = logging.getLogger(__name__)

T = TypeVar("T")

# The widest schema (CPUExtraction) is ~18 fields of {value, snippet<=200 chars},
# so a complete answer lands well under 4k tokens; the rest is headroom for the
# extraction model's reasoning tokens. Sending this explicitly matters for cost,
# not just truncation: OpenRouter reserves credit against max_tokens up front, so
# leaving it unset reserves the model's full output ceiling (65536 on
# minimax-m3) and 402s the whole run whenever the balance dips below that.
_MAX_OUTPUT_TOKENS = 8192


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


def _user_content(doc: FetchedDoc, instruction: str) -> str | list[dict[str, Any]]:
    header = f"{instruction}\nSource URL: {doc.url}"
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
        {"role": "user", "content": _user_content(doc, f"Product to extract: {target}")},
    ]

    for attempt in range(2):
        resp = await client.chat.completions.create(
            model=ChatModelConfig.get_discovery_extract_model(),
            messages=messages,
            response_format=_response_format(schema_cls),
            temperature=0,
            max_tokens=_MAX_OUTPUT_TOKENS,
            **_extra_body(session_id),
        )
        usage_events.append(_usage_from_openai(resp.usage))
        raw = resp.choices[0].message.content or ""
        try:
            return schema_cls.model_validate_json(raw)
        except ValidationError as exc:
            # Call out truncation by name: a run out of output budget otherwise
            # looks identical to a model that just emitted bad JSON.
            if resp.choices[0].finish_reason == "length":
                logger.warning(
                    "discovery: extraction from %s hit the %d-token output cap "
                    "(attempt %d) — raise _MAX_OUTPUT_TOKENS if this recurs",
                    doc.url, _MAX_OUTPUT_TOKENS, attempt + 1,
                )
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


class CandidateNames(BaseModel):
    """Sweep enumeration output: just names, no specs.

    Names are a search term, not data — each one goes back through
    search_spec_pages + extract_from_source, so a roundup's numbers never reach
    the review queue. That separation is why this call can use a loose page
    ("best GPUs of 2026") that would be a terrible extraction source."""

    model_config = ConfigDict(extra="forbid")

    names: list[str]


# What the model should be listing, per category. gpu_chipset and gpu_variant
# differ by design: one wants the silicon, the other the board-partner SKU.
_CATEGORY_NOUNS = {
    "cpu": "desktop CPU models",
    "gpu_chipset": "GPU chipsets (the silicon, e.g. \"RTX 5080\" — not board-partner cards)",
    "gpu_variant": "board-partner graphics cards (e.g. \"ASUS ROG Astral RTX 5080 OC\")",
}

# A roundup listing more than this is a spec database or a category index, not
# launch coverage; taking the first slice of it beats extracting 200 names.
_MAX_NAMES_PER_PAGE = 30

_SWEEP_SYSTEM_PROMPT = """You list PC hardware product names from a roundup or news page.

Rules:
- Return ONLY products the page presents as officially released or officially
  announced by the manufacturer, with a real product name.
- Skip anything rumored, leaked, speculated, or unconfirmed, however credible
  the page makes it sound. Signals: "leak", "rumor", "reportedly", "allegedly",
  "expected", "could launch", an unnamed source, or specs the page itself calls
  unconfirmed. When a page mixes confirmed products with rumored ones, return
  only the confirmed ones.
- Skip products mentioned as older comparisons or context.
- Use the canonical retail name a buyer would search, exactly as the page
  writes it. No marketing copy, no prices, no verdicts.
- One entry per distinct product. Never invent a product the page does not name.
- Name individual models only. Skip families, series and generations ("RTX
  60-series", "Zen 6", "Arrow Lake") — a name that covers several products
  cannot be extracted into one catalog row.
- Only list products of the requested type. If the page lists none, return an
  empty list."""


async def extract_candidate_names(
    doc: FetchedDoc,
    category: str,
    session_id: str | None,
    usage_events: list[dict],
) -> list[str]:
    """Product names one roundup page presents as new. Returns [] on any
    failure — a sweep pools names across pages, so a bad page is a smaller loss
    than a failed source is to a single-part run, and never worth a retry."""
    noun = _CATEGORY_NOUNS.get(category)
    if noun is None:
        return []

    client = _get_client()
    resp = await client.chat.completions.create(
        model=ChatModelConfig.get_discovery_extract_model(),
        messages=[
            {"role": "system", "content": _SWEEP_SYSTEM_PROMPT},
            {"role": "user", "content": _user_content(doc, f"List the {noun} on this page.")},
        ],
        response_format=_response_format(CandidateNames),
        temperature=0,
        max_tokens=_MAX_OUTPUT_TOKENS,
        **_extra_body(session_id),
    )
    usage_events.append(_usage_from_openai(resp.usage))
    raw = resp.choices[0].message.content or ""
    try:
        parsed = CandidateNames.model_validate_json(raw)
    except ValidationError as exc:
        logger.warning("discovery sweep: invalid name list from %s: %s", doc.url, exc)
        return []
    return [n.strip() for n in parsed.names[:_MAX_NAMES_PER_PAGE] if n.strip()]


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
