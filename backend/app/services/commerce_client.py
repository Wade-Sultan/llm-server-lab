from __future__ import annotations

import logging
import uuid

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# Builder -> commerce calls.
#
# Commerce owns transactional email (internal/email, Resend) and is the only
# service holding RESEND_API_KEY — see the per-service secret scoping in
# deploy/overlays/prod/patches/secrets-scoped.yaml. Rather than duplicate that
# credential and the templates into the builder image, the builder asks
# commerce to send.
#
# Plain JSON over the in-cluster Service DNS, authenticated with a shared
# secret, mirroring how admin calls the builder's discovery endpoints with
# X-Admin-Key. Deliberately not gRPC: the two services already speak HTTP+JSON
# to each other's clients, and one internal endpoint does not justify a proto
# toolchain in two languages.
#
# Only the customer's id crosses the wire, never their address: commerce reads
# users.email itself, so an address cannot go stale in transit and the builder
# never has to carry one around to send mail.

_ALERT_PATH = "/internal/v1/price-alerts"
_TIMEOUT_S = 15.0


class CommerceError(RuntimeError):
    """A call to commerce did not succeed. Callers treat this as retryable —
    the pricing ETL leaves the subscription active and tries again next run."""


class CommerceNotConfigured(CommerceError):
    """COMMERCE_INTERNAL_URL / COMMERCE_INTERNAL_KEY are unset, so there is
    nothing to call. Distinct from a failed call so a misconfigured deployment
    reads as misconfiguration rather than as commerce being down."""


def is_configured() -> bool:
    return bool(settings.COMMERCE_INTERNAL_URL and settings.COMMERCE_INTERNAL_KEY)


async def send_price_alert(
    *,
    user_id: uuid.UUID,
    part_name: str,
    old_cents: int,
    new_cents: int,
    currency: str = "USD",
    marketplace: str | None = None,
    url: str | None = None,
) -> None:
    """Ask commerce to mail one customer about one price drop.

    Returns on success; raises CommerceError on anything else. That contract is
    what lets the caller mark a subscription as sent only when the message was
    genuinely accepted.

    marketplace and url are optional because the ETL's price is a market-wide
    median across retailers, not one listing — commerce renders the message
    without the "at <retailer>" clause and without the CTA button when they are
    absent. They exist for a future alert sourced from a specific listing.
    """
    if not is_configured():
        raise CommerceNotConfigured(
            "COMMERCE_INTERNAL_URL and COMMERCE_INTERNAL_KEY must both be set"
        )

    payload: dict[str, object] = {
        "user_id": str(user_id),
        "part_name": part_name,
        "old_cents": old_cents,
        "new_cents": new_cents,
        "currency": currency,
    }
    if marketplace:
        payload["marketplace"] = marketplace
    if url:
        payload["url"] = url

    base = settings.COMMERCE_INTERNAL_URL.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.post(
                base + _ALERT_PATH,
                json=payload,
                headers={"X-Internal-Key": settings.COMMERCE_INTERNAL_KEY},
            )
    except httpx.HTTPError as exc:
        raise CommerceError(f"price alert request failed: {exc}") from exc

    if resp.status_code >= 300:
        # The body is commerce's own {"error": ...}; carrying it through is what
        # makes price_subscriptions.last_error worth reading.
        raise CommerceError(f"commerce returned {resp.status_code}: {resp.text[:200]}")
