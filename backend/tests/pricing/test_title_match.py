"""Which shopping results count as a listing for the part we searched for.

The disqualifier gate is the interesting half: these titles score *well* on
similarity — they contain the part's full name — which is exactly why a
similarity threshold alone can't keep a $3,499 prebuilt out of a GPU's price.
"""

from __future__ import annotations

import pytest

from app.services.pricing_etl import title_match


@pytest.mark.parametrize(
    "title,reason",
    [
        # Whole systems: the main source of overestimation.
        (
            "NVIDIA GeForce RTX 5080 Gaming PC Desktop Intel i9",
            title_match.REASON_SYSTEM,
        ),
        ("Prebuilt Gaming Computer with RTX 5080 32GB DDR5", title_match.REASON_SYSTEM),
        ("Pre-Built RTX 5080 Tower PC", title_match.REASON_SYSTEM),
        ("Mini-PC Ryzen 7 9800X3D 32GB", title_match.REASON_SYSTEM),
        ("Barebones Workstation Desktop Computer", title_match.REASON_SYSTEM),
        # Bundles and multipacks: a real price for more than one thing.
        ("AMD Ryzen 7 9800X3D + B650 Motherboard Bundle", title_match.REASON_BUNDLE),
        ("Ryzen 7 9800X3D CPU Combo", title_match.REASON_BUNDLE),
        ("Noctua NF-A12x25 120mm Fan 3-Pack", title_match.REASON_MULTIPACK),
        ("Arctic P12 PWM, Pack of 5", title_match.REASON_MULTIPACK),
        ("Lot of 10 Corsair RM850x Power Supplies", title_match.REASON_MULTIPACK),
        # Accessories that carry the part's name.
        ("RTX 5080 GPU Support Bracket Anti-Sag", title_match.REASON_ACCESSORY),
        ("12VHPWR Cable for RTX 5080", title_match.REASON_ACCESSORY),
        ("Thermal Paste for Ryzen 7 9800X3D", title_match.REASON_ACCESSORY),
        # Not-new: a real price for a different question (used_market_viable).
        ("Refurbished NVIDIA RTX 5080 Founders Edition", title_match.REASON_CONDITION),
        ("RTX 5080 Open-Box Excellent", title_match.REASON_CONDITION),
        ("Used AMD Ryzen 7 9800X3D CPU", title_match.REASON_CONDITION),
        ("RTX 5080 for parts, not working", title_match.REASON_CONDITION),
    ],
)
def test_disqualified_titles(title, reason):
    assert title_match.disqualifier(title) == reason


@pytest.mark.parametrize(
    "title",
    [
        "NVIDIA GeForce RTX 5080 Founders Edition 16GB GDDR7",
        "MSI GeForce RTX 5080 Gaming X Trio 16G",
        "AMD Ryzen 7 9800X3D 8-Core 4.7 GHz Socket AM5 Processor",
        "Corsair Vengeance DDR5 32GB (2x16GB) 6000MHz CL30",
        "Samsung 990 Pro 2TB PCIe 4.0 NVMe M.2 SSD",
        "NZXT H9 Flow ATX Mid-Tower Case Black",
        "Corsair RM850x 850W 80+ Gold Fully Modular ATX Power Supply",
        "Noctua NH-D15 chromax.black CPU Cooler",
        "ASUS ROG Strix B650E-F Gaming WiFi AM5 Motherboard",
    ],
)
def test_real_part_listings_are_not_disqualified(title):
    # False positives here are worse than false negatives: they thin the
    # sample, and a thin sample applies no price at all.
    assert title_match.disqualifier(title) is None


def test_a_high_similarity_prebuilt_is_still_excluded():
    query = "NVIDIA GeForce RTX 5080"
    title = "NVIDIA GeForce RTX 5080 Gaming PC Desktop"

    score = title_match.similarity(query, title)

    # It passes the similarity gate comfortably...
    assert score >= title_match.SIMILARITY_THRESHOLD
    # ...and is excluded anyway, by name rather than by score.
    assert title_match.exclusion_reason(score, title) == title_match.REASON_SYSTEM


def test_wrong_product_is_excluded_on_similarity():
    query = "AMD Ryzen 7 9800X3D"
    title = "Logitech G Pro X Superlight Wireless Mouse"

    score = title_match.similarity(query, title)

    assert (
        title_match.exclusion_reason(score, title) == title_match.REASON_LOW_SIMILARITY
    )


def test_matching_listing_is_included():
    query = "AMD Ryzen 7 9800X3D"
    title = "AMD Ryzen 7 9800X3D 8-Core Processor"

    score = title_match.similarity(query, title)

    assert title_match.exclusion_reason(score, title) is None


def test_disqualifier_beats_similarity_in_the_recorded_reason():
    # A bundle that also scores badly is labelled a bundle: the reason should
    # name the real problem, since it is what the classifier trains on.
    title = "Mega Bundle Deal"

    assert title_match.exclusion_reason(0.0, title) == title_match.REASON_BUNDLE
