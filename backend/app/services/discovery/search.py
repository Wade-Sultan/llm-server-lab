from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
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
        "hardforum.com",
        "overclock.net",
        "facebook.com",
        "x.com",
        "twitter.com",
    }
)


# Search phrasing per category for the sweep. gpu_chipset wants the silicon
# ("RTX 5080"), gpu_variant wants board-partner SKUs ("ROG Astral RTX 5080 OC"),
# so they cannot share a term.
#
# "officially launched" is load-bearing: bare "new GPU releases" ranks leak and
# rumor coverage highly, and an unreleased part has no authoritative spec page
# for the per-candidate run to extract from. The enumeration prompt rejects
# rumors too — this just stops paying to fetch them.
_CATEGORY_SWEEP_TERMS = {
    "cpu": "officially launched new desktop and workstation CPUs",
    "gpu_chipset": "officially launched new GPUs",
    "gpu_variant": "officially launched new graphics cards from board partners",
    "motherboard": "officially launched new motherboards",
    "cpu_cooler": "officially launched new CPU coolers",
    "ram_kit": "officially launched new DDR5 memory kits",
    "storage_drive": "officially launched new NVMe SSDs and hard drives",
    "psu": "officially launched new power supplies",
    "case": "officially launched new PC cases",
    "fan": "officially launched new case fans",
}

# Suffix appended to a part name when searching for its authoritative spec
# page. Most categories want a vendor spec sheet; ai_model wants a model card.
# Cases are the outlier — "specifications" on a case name ranks retailer
# listings, whose dimension tables are frequently wrong or truncated, while
# "review" ranks the outlets that actually measure clearances.
_CATEGORY_SPEC_SUFFIX = {
    "ai_model": "model card",
    "case": "specifications clearance dimensions",
}


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


async def _tavily_search(query: str, fetch_count: int) -> list[SearchResult]:
    """One Tavily call, blocked domains removed, in Tavily rank order.

    fetch_count is what we ask Tavily for, not what the caller gets — blocked
    domains are dropped after the fact, so callers over-request and slice."""
    if not settings.TAVILY_API_KEY:
        raise DiscoveryConfigError("TAVILY_API_KEY is not configured")

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            _TAVILY_URL,
            json={
                "api_key": settings.TAVILY_API_KEY,
                "query": query,
                "search_depth": "advanced",
                "max_results": fetch_count,
            },
        )
        resp.raise_for_status()

    results = []
    for r in resp.json().get("results", []):
        url = r.get("url") or ""
        if not url or _is_blocked(url):
            continue
        results.append(
            SearchResult(
                url=url, title=r.get("title") or "", score=r.get("score") or 0.0
            )
        )
    return results


async def search_spec_pages(
    query: str, category: str, max_results: int = 3
) -> list[SearchResult]:
    """Top spec-page candidates for a part name, in Tavily rank order.

    Rank order matters downstream: reconcile() breaks value ties in favor of
    the earliest source in this list."""
    suffix = _CATEGORY_SPEC_SUFFIX.get(category, "specifications")
    results = await _tavily_search(f"{query} {suffix}", fetch_count=5)
    return results[:max_results]


async def search_launch_pages(
    category: str, hint: str | None, max_results: int = 3
) -> list[SearchResult]:
    """Roundup/launch-coverage pages for a whole category — the sweep's input.

    The opposite end of search_spec_pages: that one wants the authoritative
    page for a part you can already name, this one wants editorial pages that
    *enumerate* parts, so the model has names to work from. Recency is the
    whole point, so an unhinted sweep pins the current year — "new GPUs"
    unqualified surfaces roundups from whichever year ranks best.
    """
    term = _CATEGORY_SWEEP_TERMS.get(category, "new releases")
    query = (
        f"{hint.strip()} {term}"
        if hint and hint.strip()
        else f"{term} {date.today().year}"
    )
    # Roundups repeat each other heavily, so pull a wider net than the spec-page
    # path and let the caller take the top few.
    results = await _tavily_search(query, fetch_count=8)
    return results[:max_results]
