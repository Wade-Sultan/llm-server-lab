"""
Budget-tier behaviour: the 'custom' (no ceiling) tier and the server ladder.

Pure-logic tests — no DB, no network. The point of interest is the guard that
stops 'custom' from being reachable by inference alone: it removes every price
ceiling in the pipeline, so it is the one tier where a hallucination is
expensive rather than merely wrong.
"""

from __future__ import annotations

import asyncio

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


# ---------------------------------------------------------------------------
# A stated figure beats the ladder
# ---------------------------------------------------------------------------


def test_a_stated_figure_is_spent_as_given():
    """The tier is a bucket; the number the user said is the number to spend."""
    assert cp._budget_for(_profile(budget_tier="elite", stated_budget_usd=5000)) == 5000


def test_a_stated_figure_survives_the_server_ladder():
    """THE REGRESSION. 'A $5000 LLM server' extracted as server + elite, and the
    server ladder turned it into $25000 — which cleared an $18750 GPU ceiling and
    put a $15000 workstation card in a build the chat text still called $5000."""
    profile = _profile(
        primary_use="server", budget_tier="elite", stated_budget_usd=5000
    )
    assert cp._budget_for(profile) == 5000
    assert dp._allocate_budget(cp._budget_for(profile), ["server"])["gpu"] < 5000


def test_a_stated_figure_is_not_rounded_to_its_tier():
    """$2200 and $1500 are both 'mid'; only one of them is $2200."""
    assert cp._budget_for(_profile(budget_tier="mid", stated_budget_usd=2200)) == 2200


def test_price_sensitivity_still_scales_a_stated_figure():
    """Firmness applies to the user's own number the same way it applied to the
    tier's — it says where in the band to aim, not which band."""
    firm = _profile(
        budget_tier="elite", stated_budget_usd=4000, price_sensitivity="firm"
    )
    stretch = _profile(
        budget_tier="elite", stated_budget_usd=4000, price_sensitivity="stretch"
    )
    assert cp._budget_for(firm) == 3600
    assert cp._budget_for(stretch) == 4600


def test_custom_still_wins_over_a_stated_figure():
    """A user who said cost is no object has no ceiling, whatever number they
    mentioned along the way."""
    profile = _profile(budget_tier="custom", stated_budget_usd=5000)
    assert cp._budget_for(profile) == NO_BUDGET_CEILING


def test_the_ladder_still_answers_when_no_figure_was_given():
    """Most users never name a number; the tiers remain the fallback for them."""
    assert cp._budget_for(_profile(budget_tier="mid")) == cp._BUDGET_TIER_USD["mid"]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("5000", 5000),
        ("$5,000", 5000),
        ("  2500  ", 2500),
        ("none", None),
        ("", None),
        (None, None),
        # Out of range: a figure this small buys no complete machine, and one
        # this large is a parse artifact rather than a budget. Both fall back
        # to the ladder rather than becoming a ceiling nothing can clear.
        ("50", None),
        ("999999999", None),
    ],
)
def test_stated_budget_parsing(raw, expected):
    assert cp._parse_stated_budget(raw) == expected


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


# ---------------------------------------------------------------------------
# Market drift: the ladder tracks the catalog
# ---------------------------------------------------------------------------
# The tier constants encode what a PC cost when someone last reviewed them. Part
# prices move hard, and a stale ladder fails as a cliff rather than a slope:
# ceilings stay fixed while the catalog inflates, candidate sets shrink, and
# eventually _ensure_candidates raises and every build silently falls back to a
# reference build. Drift scaling keeps the ladder's hand-tuned SHAPE (tier
# spacing, desktop vs server) and moves only its absolute level.


@pytest.fixture(autouse=True)
def _clear_drift_cache():
    """The factor is cached process-wide for 15 minutes; tests must not inherit
    each other's."""
    cp._drift_cache = None
    yield
    cp._drift_cache = None


def _drift(monkeypatch, factor):
    async def _fake(_db):
        return factor

    monkeypatch.setattr("app.crud.reference_builds.market_drift_factor", _fake)


def test_a_risen_market_raises_the_whole_ladder(monkeypatch):
    _drift(monkeypatch, 1.25)
    budget = asyncio.run(cp._budget_for_async(_profile(budget_tier="mid"), object()))
    assert budget == int(cp._BUDGET_TIER_USD["mid"] * 1.25)


def test_a_fallen_market_lowers_it(monkeypatch):
    _drift(monkeypatch, 0.8)
    budget = asyncio.run(cp._budget_for_async(_profile(budget_tier="high"), object()))
    assert budget == int(cp._BUDGET_TIER_USD["high"] * 0.8)


def test_drift_preserves_tier_spacing(monkeypatch):
    """The point of one scalar rather than a price per tier: entry stays below
    mid stays below high, in the same proportions."""
    _drift(monkeypatch, 1.4)
    figures = [
        asyncio.run(cp._budget_for_async(_profile(budget_tier=t), object()))
        for t in ("entry", "mid", "high", "elite")
    ]
    assert figures == sorted(figures)
    baseline = [cp._BUDGET_TIER_USD[t] for t in ("entry", "mid", "high", "elite")]
    for scaled, base in zip(figures, baseline, strict=True):
        assert scaled == int(base * 1.4)


def test_drift_preserves_the_server_ladder_gap(monkeypatch):
    _drift(monkeypatch, 1.3)
    desktop = asyncio.run(
        cp._budget_for_async(_profile(primary_use="ai", budget_tier="high"), object())
    )
    server = asyncio.run(
        cp._budget_for_async(
            _profile(primary_use="server", budget_tier="high"), object()
        )
    )
    assert server > desktop


def test_an_unmeasurable_market_leaves_the_ladder_alone(monkeypatch):
    """No active builds, no prices backfilled — today's behaviour exactly."""

    async def _none(_db):
        return None

    monkeypatch.setattr("app.crud.reference_builds.market_drift_factor", _none)
    budget = asyncio.run(cp._budget_for_async(_profile(budget_tier="mid"), object()))
    assert budget == cp._BUDGET_TIER_USD["mid"]


def test_a_measurement_failure_never_fails_the_build(monkeypatch):
    async def _boom(_db):
        raise RuntimeError("catalog is down")

    monkeypatch.setattr("app.crud.reference_builds.market_drift_factor", _boom)
    budget = asyncio.run(cp._budget_for_async(_profile(budget_tier="mid"), object()))
    assert budget == cp._BUDGET_TIER_USD["mid"]


@pytest.mark.parametrize(
    "measured,expected_factor",
    [(0.1, cp._DRIFT_MIN), (9.0, cp._DRIFT_MAX), (1.5, 1.5)],
)
def test_absurd_drift_is_clamped(monkeypatch, measured, expected_factor):
    """A ladder triple its tuned level is far likelier to mean a broken pricing
    ETL than a real market."""
    _drift(monkeypatch, measured)
    budget = asyncio.run(cp._budget_for_async(_profile(budget_tier="mid"), object()))
    assert budget == int(cp._BUDGET_TIER_USD["mid"] * expected_factor)


def test_a_stated_figure_is_never_scaled_by_drift(monkeypatch):
    """The user's own money means what they said it means, whatever the market
    did. Drift exists for people who never named a number."""
    _drift(monkeypatch, 2.0)
    profile = _profile(budget_tier="elite", stated_budget_usd=5000)
    assert asyncio.run(cp._budget_for_async(profile, object())) == 5000


def test_custom_is_never_scaled_by_drift(monkeypatch):
    _drift(monkeypatch, 2.0)
    profile = _profile(budget_tier="custom")
    assert asyncio.run(cp._budget_for_async(profile, object())) == NO_BUDGET_CEILING


def test_drift_is_measured_once_and_reused(monkeypatch):
    """A per-build catalog scan would be pure waste — drift moves on the
    timescale of a pricing ETL run, not a chat turn."""
    calls = 0

    async def _counting(_db):
        nonlocal calls
        calls += 1
        return 1.1

    monkeypatch.setattr("app.crud.reference_builds.market_drift_factor", _counting)
    for _ in range(5):
        asyncio.run(cp._budget_for_async(_profile(budget_tier="mid"), object()))
    assert calls == 1
