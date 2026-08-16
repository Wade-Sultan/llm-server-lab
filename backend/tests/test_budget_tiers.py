"""
Budget-tier behaviour: the 'custom' (no ceiling) tier and the server ladder.

Pure-logic tests — no DB, no network. The point of interest is the guard that
stops 'custom' from being reachable by inference alone: it removes every price
ceiling in the pipeline, so it is the one tier where a hallucination is
expensive rather than merely wrong.
"""

from __future__ import annotations

import pytest

from app.schemas.chat import NO_BUDGET_CEILING, BuildProfile, ChatMessage
from app.services import chat_pipeline as cp
from app.services.recommender import dspy_pipeline as dp


def _msgs(*user_turns: str) -> list[ChatMessage]:
    """Conversation with the given user turns, each followed by an assistant
    reply that repeats it — the assistant echo is deliberate, so the tests also
    cover the model's own words not being allowed to confirm the tier."""
    out: list[ChatMessage] = []
    for turn in user_turns:
        out.append(ChatMessage(role="user", content=turn))
        out.append(ChatMessage(role="assistant", content=f"Got it — {turn}"))
    return out


# ---------------------------------------------------------------------------
# _resolve_budget
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "money is no object",
        "Cost is no object here.",
        "there's no budget limit on this one",
        "my budget is unlimited",
        "I have an unlimited budget",
        "spend whatever you need to",
        "give me whatever it takes",
        "price doesn't matter to me",
        "I don't care about the cost",
        "build the fastest thing possible regardless of price",
        "spare no expense",
        "the sky's the limit",
    ],
)
def test_custom_confirmed_by_explicit_user_statement(statement):
    assert cp._resolve_budget("custom", None, _msgs(statement))[0] == "custom"


@pytest.mark.parametrize(
    "statement",
    [
        # Enthusiasm and superlatives are not a budget statement.
        "I want the absolute best parts you can find",
        "top of the line, no compromises on performance",
        # A figure is always the tier that figure falls in, however large.
        "I can go up to $12,000 on this",
        "budget is around 8 grand",
        "the best build you can do under $5000",
        # The dangerous near-miss: far more often "I'm broke" than "unlimited".
        "I have no budget",
        # Nothing about money at all.
        "I need it for 4K video editing",
    ],
)
def test_custom_rejected_without_explicit_statement(statement):
    assert cp._resolve_budget("custom", None, _msgs(statement))[0] == "elite"


def test_custom_not_confirmed_by_the_assistant_alone():
    """The assistant proposing 'unlimited budget' must not confirm it — that is
    the model's own words coming back around, not a second signal."""
    messages = [
        ChatMessage(role="user", content="I need a fast machine for AI work"),
        ChatMessage(role="assistant", content="Sure — is your budget unlimited?"),
    ]
    assert cp._resolve_budget("custom", None, messages)[0] == "elite"


@pytest.mark.parametrize("tier", ["entry", "mid", "high", "elite", "unknown"])
def test_other_tiers_pass_through_untouched(tier):
    """The guard only ever inspects 'custom'; an explicit statement in the
    conversation must not promote a tier the model didn't propose."""
    assert cp._resolve_budget(tier, "firm", _msgs("money is no object")) == (
        tier,
        "firm",
    )


def test_downgraded_custom_gets_a_price_sensitivity():
    """The downgrade must not leave the profile one un-askable question short.

    A downgraded 'custom' is a known tier, so is_profile_complete demands a
    price_sensitivity — but the only question that fills it is the one
    _ELICIT_SYSTEM forbids asking someone who just said cost is no object. With
    no sensitivity supplied here the turn asks forever and never builds.
    """
    tier, sensitivity = cp._resolve_budget(
        "custom", None, _msgs("honestly the budget isn't really a concern")
    )
    assert (tier, sensitivity) == ("elite", "stretch")


def test_downgraded_custom_keeps_an_extracted_sensitivity():
    """'stretch' only ever fills an absence — the extractor's answer still wins."""
    assert cp._resolve_budget(
        "custom", "firm", _msgs("honestly the budget isn't really a concern")
    ) == ("elite", "firm")


@pytest.mark.parametrize(
    "statement",
    [
        # Downgraded: too vague for the guard, permissive enough for the model.
        "honestly the budget isn't really a concern",
        # Confirmed: the guard's own wording.
        "money is no object",
        "I want the absolute best parts you can find",
    ],
)
def test_permissive_budget_talk_always_reaches_a_complete_profile(statement):
    """The regression itself: whichever way the guard rules, a profile that is
    otherwise fully specified must come out ready to build.

    Confirmed 'custom' skips the sensitivity check; a downgrade now carries one.
    Before the fix the downgrade path landed between the two and looped.
    """
    tier, sensitivity = cp._resolve_budget("custom", None, _msgs(statement))
    profile = _profile(
        primary_use="gaming",
        gaming_resolution="4k",
        gaming_fps="144",
        budget_tier=tier,
        price_sensitivity=sensitivity,
    )
    assert cp.is_profile_complete(profile)
    assert cp._missing_fields(profile) == []


# ---------------------------------------------------------------------------
# Budget totals and per-slot ceilings
# ---------------------------------------------------------------------------


def _profile(**overrides) -> BuildProfile:
    base = {"primary_use": "gaming", "budget_tier": "mid"}
    return BuildProfile(**{**base, **overrides})


def test_custom_budget_resolves_to_the_no_ceiling_sentinel():
    assert cp._budget_for(_profile(budget_tier="custom")) == NO_BUDGET_CEILING


def test_server_tiers_are_scaled_above_the_desktop_ladder():
    """A Threadripper and its board alone clear the desktop 'elite' total, so a
    server profile must not be sized on the desktop ladder."""
    desktop = cp._budget_for(_profile(primary_use="ai", budget_tier="high"))
    server = cp._budget_for(_profile(primary_use="server", budget_tier="high"))
    assert server > desktop


def test_custom_overrides_the_server_ladder_too():
    assert (
        cp._budget_for(_profile(primary_use="server", budget_tier="custom"))
        == NO_BUDGET_CEILING
    )


def test_no_ceiling_propagates_to_every_slot():
    """Not a large share of a large number — every slot gets the sentinel, so
    the CRUD layer drops its price filter rather than raising it."""
    budget = dp._allocate_budget(NO_BUDGET_CEILING, ["server"])
    assert set(budget) == {
        "cpu",
        "cooler",
        "mobo",
        "ram",
        "storage",
        "gpu",
        "psu",
        "case",
        "fans",
    }
    assert all(v == NO_BUDGET_CEILING for v in budget.values())


def test_ordinary_budget_still_splits_per_slot():
    budget = dp._allocate_budget(2000, ["server"])
    assert all(0 < v < 2000 for v in budget.values())
    # The server profile is the only one where the platform outweighs the GPU
    # in every slot except the GPU itself; the board share is well above the
    # consumer profiles' 0.10.
    assert budget["mobo"] > dp._allocate_budget(2000, ["gaming"])["mobo"]


# ---------------------------------------------------------------------------
# Downstream consumers of the tier
# ---------------------------------------------------------------------------


def test_custom_is_a_complete_profile():
    """'custom' is a real answer about budget, so it must not read as 'unknown'
    and send the conversation back to elicitation."""
    profile = _profile(
        primary_use="gaming",
        budget_tier="custom",
        gaming_resolution="4k",
        gaming_fps="144",
    )
    assert cp.is_profile_complete(profile)
    assert "budget expectations" not in cp._missing_fields(profile)


# ---------------------------------------------------------------------------
# The sentinel at the CRUD boundary
# ---------------------------------------------------------------------------
# The whole point of NO_BUDGET_CEILING is that it stops being a comparison at
# all. Asserted against the compiled SQL because that is where a regression
# would actually bite: a ceiling that silently survives as `price <= -100`
# returns zero candidates and fails the run at the first step.


def _compiled(predicate) -> str:
    from sqlalchemy.dialects import postgresql

    return str(predicate.compile(dialect=postgresql.dialect()))


def test_ordinary_ceiling_compiles_to_a_price_comparison():
    from app.crud.components import _within_budget
    from app.models.pcparts import CPU

    sql = _compiled(_within_budget(CPU.street_price_cents, 300))
    assert "street_price_cents" in sql
    assert "<=" in sql


def test_no_ceiling_compiles_away_entirely():
    from app.crud.components import _within_budget
    from app.models.pcparts import CPU

    sql = _compiled(_within_budget(CPU.street_price_cents, NO_BUDGET_CEILING))
    assert "street_price_cents" not in sql
    assert sql.lower().strip() == "true"
