// Client for the price-alert API (backend app/api/routes/price_subscriptions.py).
//
// A user asks to be told when a part gets cheaper; the pricing ETL decides
// whether that has happened and commerce sends the mail. Nothing here polls or
// evaluates anything — these calls only manage who is waiting for what.
//
// Targets are the catch: a build carries pc_parts ids, but for GPU/PSU/RAM/
// storage the price lives on a group row, and that is what the ETL prices. The
// server resolves that redirect (see lookupPriceTargets), so a caller must
// subscribe to the *resolved* target_kind/target_id it hands back rather than
// to the part id it started from — otherwise the subscription watches a column
// nothing writes and the alert never fires.

import { getAccessToken } from "@/hooks/useAuth"

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"

export interface PriceSubscription {
  id: string
  target_kind: string
  target_id: string
  target_name: string | null
  /** Alert when the price reaches this. Null means "any drop". */
  threshold_cents: number | null
  /** The price when the subscription was created — what "any drop" is measured from. */
  baseline_price_cents: number | null
  current_price_cents: number | null
  status: string
  created_at: string
  notified_at: string | null
  notified_price_cents: number | null
}

/** A part resolved to something watchable, plus the caller's own subscription. */
export interface PriceTarget {
  part_id: string
  target_kind: string
  target_id: string
  target_name: string
  /**
   * The catalog street price, in cents. This — not the marketplace listing
   * shown beside it on the card — is what alerts are evaluated against, so it
   * is the price the alert dialog quotes.
   */
  current_price_cents: number | null
  active_count: number
  subscription: PriceSubscription | null
}

/** A failed call, carrying the status so the UI can explain itself. */
export class PriceAlertError extends Error {
  readonly status: number

  constructor(status: number, body: string) {
    super(`price alert request failed: ${status} ${body}`.trim())
    this.name = "PriceAlertError"
    this.status = status
  }
}

async function failed(res: Response): Promise<PriceAlertError> {
  const body = await res.text().catch(() => "")
  return new PriceAlertError(res.status, body.slice(0, 200))
}

async function authHeaders(): Promise<Record<string, string>> {
  const token = await getAccessToken()
  return token
    ? { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }
    : { "Content-Type": "application/json" }
}

/**
 * Resolve a card's parts to their price targets, keyed by the part_id asked
 * about. One request for the whole card.
 *
 * Authenticated when there is a session and anonymous otherwise — the price
 * and watcher count are public, and `subscription` is simply null for a guest,
 * which is what lets the shared-build page render the same card.
 *
 * Parts the server can't resolve (no price anywhere, or an exact with no
 * group) are absent from the result: the card hides the bell for those rather
 * than offering an alert that could never fire. A failed request is an empty
 * result for the same reason — no bells, rather than bells that error on click.
 */
export async function lookupPriceTargets(
  partIds: string[],
): Promise<Record<string, PriceTarget>> {
  const ids = partIds.filter(Boolean)
  if (ids.length === 0) return {}

  try {
    const res = await fetch(
      `${API_BASE}/api/v1/price-subscriptions/lookup?part_ids=${encodeURIComponent(ids.join(","))}`,
      { headers: await authHeaders() },
    )
    if (!res.ok) return {}
    const targets: PriceTarget[] = await res.json()
    return Object.fromEntries(targets.map((t) => [t.part_id, t]))
  } catch {
    return {}
  }
}

/**
 * Start (or retarget) a watch. `thresholdCents` null means "tell me about any
 * drop from today's price".
 *
 * Idempotent server-side: subscribing again to a target you already watch
 * updates the threshold and re-anchors the baseline instead of erroring, so
 * this doubles as the edit call.
 */
export async function createPriceSubscription(args: {
  targetKind: string
  targetId: string
  thresholdCents: number | null
}): Promise<PriceSubscription> {
  const res = await fetch(`${API_BASE}/api/v1/price-subscriptions`, {
    method: "POST",
    headers: await authHeaders(),
    body: JSON.stringify({
      target_kind: args.targetKind,
      target_id: args.targetId,
      threshold_cents: args.thresholdCents,
    }),
  })
  if (!res.ok) throw await failed(res)
  return res.json()
}

/** The caller's watches, newest first. Active only unless asked otherwise. */
export async function listPriceSubscriptions(
  includeInactive = false,
): Promise<PriceSubscription[]> {
  const res = await fetch(
    `${API_BASE}/api/v1/price-subscriptions?include_inactive=${includeInactive}`,
    { headers: await authHeaders() },
  )
  if (!res.ok) throw await failed(res)
  return res.json()
}

/** Stop watching. The row is canceled server-side, not deleted. */
export async function cancelPriceSubscription(id: string): Promise<void> {
  const res = await fetch(
    `${API_BASE}/api/v1/price-subscriptions/${encodeURIComponent(id)}`,
    { method: "DELETE", headers: await authHeaders() },
  )
  if (!res.ok) throw await failed(res)
}
