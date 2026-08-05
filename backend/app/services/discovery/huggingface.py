"""Hugging Face Hub as a first-class discovery source for the ai_model category.

Every other category goes search -> fetch -> LLM extraction, because hardware
specs only exist as prose on vendor pages. AI models don't have that problem:
the Hub publishes the same fields as structured JSON, including an exact
parameter count read off the safetensors index rather than a marketing claim.
So the ai_model path skips Tavily and the extraction model entirely, which
makes it both free and non-hallucinating.

What it deliberately does NOT produce is the ai_workloads matrix. A workload
row is a curated engineering claim about VRAM at a given task and precision;
the Hub has no opinion on that. This populates the ai_models catalog entity
only, and workload rows stay hand-authored in the admin.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_API = "https://huggingface.co/api"
_TIMEOUT = 20.0

# The Hub's pipeline_tag is the closest thing to a family discriminator it
# publishes. Anything unmapped is skipped rather than guessed at — a model
# staged under the wrong family would be matched against the wrong workloads.
_PIPELINE_TO_FAMILY: dict[str, str] = {
    "text-generation": "llm",
    "text2text-generation": "llm",
    "fill-mask": "llm",
    "summarization": "llm",
    "translation": "llm",
    "image-text-to-text": "multimodal",
    "visual-question-answering": "multimodal",
    "video-text-to-text": "multimodal",
    "any-to-any": "multimodal",
    "text-to-image": "image_gen",
    "image-to-image": "image_gen",
    "unconditional-image-generation": "image_gen",
    "text-to-video": "video_gen",
    "image-to-video": "video_gen",
    "automatic-speech-recognition": "speech",
    "text-to-speech": "speech",
    "voice-activity-detection": "speech",
    "text-to-audio": "audio_gen",
    "audio-to-audio": "audio_gen",
    "image-classification": "vision",
    "object-detection": "vision",
    "image-segmentation": "vision",
    "depth-estimation": "vision",
    "zero-shot-image-classification": "vision",
    "keypoint-detection": "vision",
    "sentence-similarity": "embedding",
    "feature-extraction": "embedding",
    "tabular-classification": "classical",
    "tabular-regression": "classical",
    "reinforcement-learning": "rl",
    "robotics": "rl",
}

# Parameter counts in a model name: "Llama-3.1-70B", "Qwen3-0.6B", "gpt-oss-120b".
_PARAMS_IN_NAME = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*[bB](?![\w])")

# Config keys that mean "context window", newest naming first. Different
# architectures use different keys and some publish several; the first present
# wins rather than the largest, because a model that publishes both a trained
# and an extended window lists the trained one first.
_CONTEXT_KEYS = (
    "max_position_embeddings",
    "n_positions",
    "max_sequence_length",
    "seq_length",
)


class HuggingFaceError(RuntimeError):
    """The Hub API could not be reached or returned an unusable response."""


@dataclass
class HubModel:
    """One Hub model, already shaped like ai_models columns."""

    hub_id: str  # e.g. "meta-llama/Llama-3.1-70B-Instruct"
    fields: dict[str, Any]  # ai_models column names -> values
    provenance: dict[str, dict]  # same shape the LLM path produces


def _headers() -> dict[str, str]:
    if settings.HF_TOKEN:
        return {"Authorization": f"Bearer {settings.HF_TOKEN}"}
    return {}


async def _get(client: httpx.AsyncClient, path: str, **params: Any) -> Any:
    resp = await client.get(f"{_API}{path}", params=params or None)
    resp.raise_for_status()
    return resp.json()


def _params_billions(detail: dict, hub_id: str) -> float | None:
    """Exact parameter count when the Hub has one, else the name's claim.

    safetensors.total is summed from the real weight index, so it is the only
    non-marketing parameter count available anywhere. It is absent for models
    published in other formats (GGUF-only repos, older .bin checkpoints), which
    is the one case where falling back to the name is better than nothing —
    the name is where the number came from originally.
    """
    total = (detail.get("safetensors") or {}).get("total")
    if isinstance(total, int) and total > 0:
        return round(total / 1e9, 3)

    match = _PARAMS_IN_NAME.search(hub_id.rsplit("/", 1)[-1])
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def _context_length(detail: dict) -> int | None:
    config = detail.get("config") or {}
    for key in _CONTEXT_KEYS:
        value = config.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return None


def _license(detail: dict) -> str | None:
    card = detail.get("cardData") or {}
    if isinstance(card.get("license"), str):
        return card["license"]
    # Not every repo fills cardData; the Hub also mirrors the license into the
    # tag list as "license:apache-2.0".
    for tag in detail.get("tags") or []:
        if isinstance(tag, str) and tag.startswith("license:"):
            return tag.removeprefix("license:")
    return None


def _display_name(hub_id: str) -> str:
    """Human-facing name from a Hub id: the repo half, dashes to spaces.

    "meta-llama/Llama-3.1-70B-Instruct" -> "Llama 3.1 70B Instruct". Kept
    deliberately mechanical — this is the reviewer's editable default in the
    approval form, not a final answer, and a cleverer transform would just be
    a second thing to disagree with the Hub about.
    """
    return hub_id.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ").strip()


def _slug(hub_id: str) -> str:
    """ai_models.slug is unique, and so is a Hub id — derive one from the other
    so re-discovering a model can never mint a second slug for it."""
    return re.sub(r"[^a-z0-9]+", "-", hub_id.lower()).strip("-")


def _to_hub_model(detail: dict) -> HubModel | None:
    """Shape one Hub API payload into ai_models fields. None = unusable."""
    hub_id = detail.get("id") or detail.get("modelId")
    if not isinstance(hub_id, str) or "/" not in hub_id:
        return None

    family = _PIPELINE_TO_FAMILY.get(detail.get("pipeline_tag") or "")
    if family is None:
        logger.debug(
            "hf discovery: skipping %s — unmapped pipeline_tag %r",
            hub_id,
            detail.get("pipeline_tag"),
        )
        return None

    url = f"https://huggingface.co/{hub_id}"
    fields: dict[str, Any] = {
        "name": _display_name(hub_id),
        "slug": _slug(hub_id),
        "family": family,
        "huggingface_id": hub_id,
        "developer": detail.get("author") or hub_id.split("/", 1)[0],
        "website_url": url,
        "params_billions": _params_billions(detail, hub_id),
        "context_length": _context_length(detail),
        "license": _license(detail),
    }
    fields = {k: v for k, v in fields.items() if v is not None}

    # Every field came from one JSON document, so provenance is uniform. The
    # snippet names the API field rather than quoting prose — there is no prose
    # to quote, and "which key did this come from" is what a reviewer checking
    # a Hub-sourced value actually wants to know.
    provenance = {
        key: {"source_url": f"{_API}/models/{hub_id}", "snippet": f"Hub field: {key}"}
        for key in fields
    }
    return HubModel(hub_id=hub_id, fields=fields, provenance=provenance)


async def fetch_model(hub_id: str) -> HubModel | None:
    """One model by exact Hub id. None if it doesn't exist or isn't usable."""
    async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_headers()) as client:
        try:
            detail = await _get(client, f"/models/{hub_id}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise HuggingFaceError(
                f"Hugging Face Hub returned {exc.response.status_code} for {hub_id}"
            ) from exc
        except httpx.HTTPError as exc:
            raise HuggingFaceError(f"Cannot reach the Hugging Face Hub: {exc}") from exc
    return _to_hub_model(detail)


async def _list_and_hydrate(params: dict[str, Any], limit: int) -> list[HubModel]:
    """Run a /models listing, then re-fetch each hit in full.

    Two calls per model, not one: the list endpoint omits `config` and
    `safetensors`, which are exactly the fields worth having (context window
    and exact parameter count), so a listing alone would stage every model with
    both of them null.
    """
    # Over-fetch: unmapped pipeline tags are dropped during hydration, so
    # asking for exactly `limit` would under-deliver on any query that surfaces
    # untagged or adapter-only repos.
    params = {**params, "limit": max(limit * 3, 15), "direction": -1}

    async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_headers()) as client:
        try:
            listed = await _get(client, "/models", **params)
        except httpx.HTTPError as exc:
            raise HuggingFaceError(f"Hugging Face Hub listing failed: {exc}") from exc
        if not isinstance(listed, list):
            raise HuggingFaceError("Hugging Face Hub returned an unexpected body")

        models: list[HubModel] = []
        for entry in listed:
            hub_id = entry.get("id") or entry.get("modelId")
            if not isinstance(hub_id, str):
                continue
            # One repo failing is not worth losing the rest of the batch.
            try:
                detail = await _get(client, f"/models/{hub_id}")
            except httpx.HTTPError:
                logger.warning("hf discovery: detail fetch failed for %s", hub_id)
                continue
            model = _to_hub_model(detail)
            if model is not None:
                models.append(model)
            if len(models) == limit:
                break
    return models


async def search_models(query: str, limit: int = 1) -> list[HubModel]:
    """Resolve a free-text name to Hub models, most-downloaded first.

    Downloads rather than trending here: a named lookup wants the canonical
    repo for that name, and cumulative downloads is what distinguishes the
    official weights from the hundred quantized re-uploads of them.
    """
    return await _list_and_hydrate({"search": query, "sort": "downloads"}, limit)


async def list_trending(hint: str | None, limit: int) -> list[HubModel]:
    """The ai_model sweep's enumerator: what the Hub is currently surfacing.

    Sorted by trending score rather than downloads, the opposite of
    search_models and for the opposite reason. Downloads are cumulative, so
    that ranking returns the same established models every month — useless to
    a sweep. Trending surfaces recent releases, which is what the hardware
    sweeps get from their "officially launched" search terms.
    """
    params: dict[str, Any] = {"sort": "trendingScore"}
    if hint and hint.strip():
        params["search"] = hint.strip()
    return await _list_and_hydrate(params, limit)
