from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.crud import price_subscriptions as crud
from app.services import commerce_client

logger = logging.getLogger(__name__)

# Price alerts fire here, inside the pricing ETL, and nowhere else.
#
# The ETL is the only thing that ever moves street_price_cents, so it is the
# only place that can observe a drop rather than infer one. Evaluating here also
# means an alert cannot be sent for a price that was never written: the decision
# reads the same before/after pair the job just committed.

# Reasons a subscription did not fire, recorded in the run log. Useful mostly
# in dry-run, where the whole point is seeing what *would* have gone out.
SKIP_NO_PRICE = "no_new_price"
SKIP_NO_REFERENCE = "no_reference_price"
SKIP_ABOVE_THRESHOLD = "above_threshold"
SKIP_NOT_A_DROP = "not_a_drop"


@dataclass(frozen=True)
class Decision:
    fire: bool
    reason: str
    # The "was" price the email shows. Only meaningful when fire is True.
    old_cents: int | None = None


def decide(
    *,
    threshold_cents: int | None,
    baseline_cents: int | None,
    previous_cents: int | None,
    new_cents: int | None,
) -> Decision:
    """Should this subscription fire, given what the check just found?

    Pure, and separated from the dispatch around it, because this is the part
    with the interesting edges: three nullable prices, and an email that must
    be able to say "was $X, now $Y" truthfully or not be sent at all.

    - threshold_cents set     -> fire at or below it
    - threshold_cents unset   -> fire below the price when the user subscribed
    - old price for the email -> the last price we actually published
      (previous_cents), falling back to the subscribe-time baseline
    """
    if new_cents is None:
        return Decision(False, SKIP_NO_PRICE)

    target = threshold_cents if threshold_cents is not None else baseline_cents
    if target is None:
        # No threshold and no baseline: nothing to compare against. Happens
        # when a user subscribes to a part that has never been priced.
        return Decision(False, SKIP_NO_REFERENCE)

    if new_cents > target:
        return Decision(False, SKIP_ABOVE_THRESHOLD)

    old_cents = previous_cents if previous_cents is not None else baseline_cents
    if old_cents is None or old_cents <= new_cents:
        # Crossing the threshold without falling is not a price drop — it means
        # the threshold was already met when the user subscribed, or the price
        # rose into range from below. Commerce rejects a non-drop outright, so
        # catching it here keeps that from looking like a dispatch failure.
        return Decision(False, SKIP_NOT_A_DROP)

    return Decision(True, "fire", old_cents=old_cents)


@dataclass
class Outcome:
    """What one target's evaluation did. Aggregated into the run row's
    alerts_sent and into the run log."""

    considered: int = 0
    fired: int = 0
    sent: int = 0
    failed: int = 0
    dry_run: int = 0


async def process_target(
    db: AsyncSession,
    *,
    target_kind: str,
    target_id: uuid.UUID,
    target_name: str | None,
    previous_cents: int | None,
    new_cents: int | None,
) -> Outcome:
    """Evaluate every active subscription on one target and dispatch the ones
    that should fire. Never raises: an alerting failure must not fail the
    pricing run that produced the price, which is the more valuable output.
    """
    outcome = Outcome()
    if new_cents is None:
        return outcome

    try:
        subs = await crud.list_active_for_target(db, target_kind, target_id)
    except Exception:
        logger.exception("alerts: failed to load subscriptions for %s", target_id)
        return outcome

    if not subs:
        return outcome

    outcome.considered = len(subs)
    for sub in subs:
        decision = decide(
            threshold_cents=sub.threshold_cents,
            baseline_cents=sub.baseline_price_cents,
            previous_cents=previous_cents,
            new_cents=new_cents,
        )
        if not decision.fire:
            logger.debug(
                "alerts: subscription %s not firing (%s)", sub.id, decision.reason
            )
            continue

        outcome.fired += 1

        # The flag is the whole "infrastructure without users" posture: with it
        # off, everything above still runs — the subscriptions are loaded, the
        # decision is made, the run log shows exactly who would have been
        # mailed — but nothing leaves the cluster and no row is retired, so
        # flipping it on later alerts the same people it would have today.
        if not settings.PRICE_ALERTS_ENABLED:
            outcome.dry_run += 1
            logger.info(
                "alerts: DRY RUN — would alert user %s about %s (%s): %s -> %s cents",
                sub.user_id,
                target_name or target_id,
                target_kind,
                decision.old_cents,
                new_cents,
            )
            continue

        try:
            await commerce_client.send_price_alert(
                user_id=sub.user_id,
                part_name=target_name or "a part you're tracking",
                old_cents=decision.old_cents or 0,
                new_cents=new_cents,
            )
        except commerce_client.CommerceError as exc:
            outcome.failed += 1
            logger.warning(
                "alerts: dispatch failed for subscription %s: %s", sub.id, exc
            )
            await crud.record_failure(db, sub.id, error=str(exc))
            continue

        await crud.mark_sent(db, sub.id, price_cents=new_cents)
        outcome.sent += 1
        logger.info(
            "alerts: notified user %s about %s (%s -> %s cents)",
            sub.user_id,
            target_name or target_id,
            decision.old_cents,
            new_cents,
        )

    return outcome
