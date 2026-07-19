from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_TAVILY_URL = "https://api.tavily.com/search"

# Domains that never carry authoritative spec sheets — video, forums, and
# aggregators whose numbers are user-submitted. PCPartPicker is additionally
# off-limits by ToS.
_BLOCKED_DOMAINS = frozenset(
    {
        "youtube.com",
        "youtu.be",
        "reddit.com",
        "quora.com",
        "pcpartpicker.com",
        "forums.tomshardware.com",
        "linustechtips.com",
        "facebook.com",
        "x.com",
        "twitter.com",
    }
)


class DiscoveryConfigError(RuntimeError):
    """A discovery run cannot start because required config is missing."""


@dataclass
class SearchResult:
    url: str
    title: str
    score: float


def _is_blocked(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return any(host == d or host.endswith("." + d) for d in _BLOCKED_DOMAINS)


async def search_spec_pages(
    query: str, category: str, max_results: int = 3
) -> list[SearchResult]:
    """Top spec-page candidates for a part name, in Tavily rank order.

    Rank order matters downstream: reconcile() breaks value ties in favor of
    the earliest source in this list."""
    if not settings.TAVILY_API_KEY:
        raise DiscoveryConfigError("TAVILY_API_KEY is not configured")

    suffix = "model card" if category == "ai_model" else "specifications"
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            _TAVILY_URL,
            json={
                "api_key": settings.TAVILY_API_KEY,
                "query": f"{query} {suffix}",
                "search_depth": "advanced",
                "max_results": 5,
            },
        )
        resp.raise_for_status()

    results = []
    for r in resp.json().get("results", []):
        url = r.get("url") or ""
        if not url or _is_blocked(url):
            continue
        results.append(
            SearchResult(url=url, title=r.get("title") or "", score=r.get("score") or 0.0)
        )
    return results[:max_results]
