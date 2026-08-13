from __future__ import annotations

import re

# Deciding whether a shopping result is actually the part we searched for.
#
# Two independent gates, because they catch different failures:
#
#   similarity  — is this the same product? Catches the wrong SKU, the wrong
#                 capacity, a different generation.
#   disqualifiers — is this a *part* at all? Catches the listings that score
#                 well precisely because they contain the part's full name: a
#                 prebuilt desktop built around it, a bundle, a five-pack, a
#                 replacement bracket for it.
#
# The second gate is the one that matters for price accuracy. A high-similarity
# whole-system listing is the single biggest source of overestimation, and no
# similarity threshold can exclude it — "NVIDIA GeForce RTX 5080 Gaming Desktop
# PC" is, by every token measure, an excellent match for "NVIDIA GeForce RTX
# 5080".

# Below this, a result is treated as "probably not the part we searched for"
# (wrong SKU, wrong capacity, ...) and excluded from the price stats — but
# every raw result is still stored with its score and its reason, so this
# threshold isn't a hard data-loss point, just a stats-inclusion gate. The
# eventual ML classifier trains on exactly this signal plus the ones it rejects.
SIMILARITY_THRESHOLD = 45.0

REASON_LOW_SIMILARITY = "low_similarity"
REASON_SYSTEM = "whole_system"
REASON_BUNDLE = "bundle"
REASON_MULTIPACK = "multipack"
REASON_ACCESSORY = "accessory"
REASON_CONDITION = "not_new"

# Ordered most-specific first: the reason recorded is the first pattern that
# matches, and a "refurbished gaming PC bundle" is more usefully labelled a
# whole system than a condition.
_DISQUALIFIERS: list[tuple[str, re.Pattern[str]]] = [
    (
        REASON_SYSTEM,
        re.compile(
            r"\b(gaming (pc|desktop|computer|system)"
            r"|desktop (pc|computer)"
            r"|pre-?built"
            r"|barebones?"
            r"|mini[- ]pc"
            r"|all[- ]in[- ]one"
            r"|workstation (pc|desktop)"
            r"|complete (pc|system|build)"
            r"|tower (pc|computer))\b",
            re.I,
        ),
    ),
    (
        REASON_BUNDLE,
        re.compile(
            r"\b(bundle|combo|bundled with|w/ motherboard|with motherboard)\b", re.I
        ),
    ),
    (
        REASON_MULTIPACK,
        # "3-pack", "3 pack", "pack of 5", "lot of 10". Guards the count so a
        # legitimate "2 Pack" of fans is still caught but "PACKARD" is not.
        re.compile(r"\b(\d+\s*-?\s*pack|pack of \d+|lot of \d+)\b", re.I),
    ),
    (
        REASON_ACCESSORY,
        re.compile(
            r"\b(cable|adapter|bracket|riser|extender|screws?"
            r"|thermal (paste|pad)"
            r"|dust ?cover|anti[- ]sag|support bracket"
            r"|sticker|decal|keychain|t-?shirt|poster"
            r"|box only|empty box|manual)\b",
            re.I,
        ),
    ),
    (
        REASON_CONDITION,
        # street_price_cents means the price of a new one. Used and refurbished
        # listings are real prices for a different question (see
        # pc_parts.used_market_viable) and would bias this one downward.
        re.compile(
            r"\b(refurb(ished)?|renewed|pre-?owned|open[- ]box"
            r"|for parts|not working|as[- ]is|\bused\b)\b",
            re.I,
        ),
    ),
]


def similarity(part_title: str, result_title: str) -> float:
    """Lightweight title-consistency score in [0, 100]. Token-sort so word
    order differences ("RTX 5080 MSI Gaming X Trio" vs "MSI Gaming X Trio RTX
    5080") don't tank the score."""
    from rapidfuzz import fuzz  # lazy: pricing ETL job only, off the API import path

    return fuzz.token_sort_ratio(part_title, result_title)


def disqualifier(result_title: str) -> str | None:
    """The reason this title is not a listing for a single new part, or None.

    Checked independently of similarity: these titles score *well*, which is
    exactly why they need their own gate.
    """
    for reason, pattern in _DISQUALIFIERS:
        if pattern.search(result_title):
            return reason
    return None


def exclusion_reason(score: float, result_title: str) -> str | None:
    """Why this result should stay out of the price stats, or None to include
    it. Disqualifiers are checked first so the recorded reason names the real
    problem rather than a similarity score that may well have been fine."""
    reason = disqualifier(result_title)
    if reason is not None:
        return reason
    if score < SIMILARITY_THRESHOLD:
        return REASON_LOW_SIMILARITY
    return None
