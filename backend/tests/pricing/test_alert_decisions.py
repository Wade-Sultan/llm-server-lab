"""When a price subscription fires.

Pure-logic tests over alerts.decide — the part with the interesting edges.
Three nullable prices meet an email that can only truthfully say "was $X, now
$Y", so every combination has to resolve to either a real drop or a clean
refusal; commerce rejects a non-drop outright, and a refusal that gets that far
looks like a dispatch failure instead of the deliberate no-op it is.

The rule under all of it is a crossing: above the target before, at or below it
after. A price that was already under the customer's number has nothing to
announce, however far it falls afterwards.
"""

from __future__ import annotations

from app.services.pricing_etl import alerts


def test_threshold_met_by_a_falling_price_fires():
    d = alerts.decide(
        threshold_cents=45000,
        baseline_cents=54999,
        previous_cents=52000,
        new_cents=42950,
    )

    assert d.fire
    # "Was" is the last price we published, not the subscribe-time baseline:
    # it is what the customer would have seen on the site yesterday.
    assert d.old_cents == 52000


def test_price_still_above_the_threshold_does_not_fire():
    d = alerts.decide(
        threshold_cents=40000,
        baseline_cents=54999,
        previous_cents=52000,
        new_cents=42950,
    )

    assert not d.fire
    assert d.reason == alerts.SKIP_ABOVE_THRESHOLD


def test_price_exactly_at_the_threshold_fires():
    d = alerts.decide(
        threshold_cents=42950,
        baseline_cents=54999,
        previous_cents=52000,
        new_cents=42950,
    )

    assert d.fire


def test_no_threshold_fires_on_any_drop_below_the_baseline():
    d = alerts.decide(
        threshold_cents=None,
        baseline_cents=54999,
        previous_cents=54999,
        new_cents=54499,
    )

    assert d.fire
    assert d.old_cents == 54999


def test_no_threshold_does_not_fire_on_a_rise():
    d = alerts.decide(
        threshold_cents=None,
        baseline_cents=54999,
        previous_cents=54999,
        new_cents=56000,
    )

    assert not d.fire
    assert d.reason == alerts.SKIP_ABOVE_THRESHOLD


def test_a_drop_that_never_crosses_the_threshold_does_not_fire():
    # Asked about $500 on a part already at $450, which has since slid to $440.
    # A real drop, but not the event they subscribed for: the part reached
    # their price before they asked, so there is no crossing to report.
    d = alerts.decide(
        threshold_cents=50000,
        baseline_cents=45000,
        previous_cents=45000,
        new_cents=44000,
    )

    assert not d.fire
    assert d.reason == alerts.SKIP_ALREADY_BELOW


def test_a_price_that_rose_above_the_threshold_and_came_back_fires():
    # The other side of the same rule: once the price left the range, returning
    # to it is a crossing again, and the one the customer is waiting on.
    d = alerts.decide(
        threshold_cents=50000,
        baseline_cents=45000,
        previous_cents=52000,
        new_cents=49000,
    )

    assert d.fire
    assert d.old_cents == 52000


def test_a_price_sitting_exactly_on_the_threshold_has_not_crossed_it():
    # "At or below" is met by the old price too, so this run is not the moment
    # the part reached their number — the previous one was.
    d = alerts.decide(
        threshold_cents=45000,
        baseline_cents=54999,
        previous_cents=45000,
        new_cents=44000,
    )

    assert not d.fire
    assert d.reason == alerts.SKIP_ALREADY_BELOW


def test_no_threshold_fires_on_the_first_drop_under_the_baseline():
    # The crossing check is not applied to "any drop": the target there *is*
    # the subscribe-time price, and a part that has not moved since sits
    # exactly on it. Requiring the old price to be strictly above would make
    # this subscription unfireable.
    d = alerts.decide(
        threshold_cents=None,
        baseline_cents=45000,
        previous_cents=45000,
        new_cents=44000,
    )

    assert d.fire
    assert d.old_cents == 45000


def test_threshold_already_met_when_they_subscribed_is_not_a_drop():
    # The customer asked to hear about $500 on a part already at $450, and the
    # price has since risen to $480. It is under the threshold and going the
    # wrong way — mailing "it dropped" would be false.
    d = alerts.decide(
        threshold_cents=50000,
        baseline_cents=45000,
        previous_cents=45000,
        new_cents=48000,
    )

    assert not d.fire
    assert d.reason == alerts.SKIP_NOT_A_DROP


def test_unchanged_price_is_not_a_drop():
    d = alerts.decide(
        threshold_cents=50000,
        baseline_cents=45000,
        previous_cents=45000,
        new_cents=45000,
    )

    assert not d.fire
    assert d.reason == alerts.SKIP_NOT_A_DROP


def test_baseline_stands_in_when_there_is_no_previous_price():
    # The part had no street price at all until this run — its first check is
    # also the one that triggers the alert.
    d = alerts.decide(
        threshold_cents=45000,
        baseline_cents=54999,
        previous_cents=None,
        new_cents=42950,
    )

    assert d.fire
    assert d.old_cents == 54999


def test_no_reference_price_at_all_does_not_fire():
    # Subscribed to a part that has never been priced, and asked for "any
    # drop": there is nothing to have dropped from.
    d = alerts.decide(
        threshold_cents=None,
        baseline_cents=None,
        previous_cents=None,
        new_cents=42950,
    )

    assert not d.fire
    assert d.reason == alerts.SKIP_NO_REFERENCE


def test_threshold_met_with_no_old_price_anywhere_does_not_fire():
    # A threshold gives something to compare against, but the email still has
    # no honest "was $X" to show.
    d = alerts.decide(
        threshold_cents=45000,
        baseline_cents=None,
        previous_cents=None,
        new_cents=42950,
    )

    assert not d.fire
    assert d.reason == alerts.SKIP_NOT_A_DROP


def test_a_check_that_applied_no_price_does_not_fire():
    # The run found too few usable results to trust a figure. Nothing changed,
    # so nothing is announced.
    d = alerts.decide(
        threshold_cents=45000,
        baseline_cents=54999,
        previous_cents=52000,
        new_cents=None,
    )

    assert not d.fire
    assert d.reason == alerts.SKIP_NO_PRICE
