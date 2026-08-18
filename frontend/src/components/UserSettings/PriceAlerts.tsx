"use client"

import { BellIcon } from "lucide-react"
import { useCallback, useEffect, useState } from "react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import useAuth from "@/hooks/useAuth"
import {
  cancelPriceSubscription,
  listPriceSubscriptions,
  type PriceSubscription,
} from "@/lib/priceAlerts"
import { formatCents } from "@/lib/utils"

/**
 * The parts a user is waiting on a price drop for, and the only place to call
 * one off without finding the build it came from again.
 *
 * Active subscriptions only. A watch is retired the moment its alert is sent
 * (one email per subscription, by design — see the model), so listing sent and
 * canceled rows here would be a history of things that are no longer running,
 * under a heading that says they are.
 */
export default function PriceAlerts() {
  const { user, loading: authLoading } = useAuth()
  const [subs, setSubs] = useState<PriceSubscription[] | null>(null)
  const [failed, setFailed] = useState(false)
  const [removing, setRemoving] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!user) return
    try {
      setSubs(await listPriceSubscriptions())
      setFailed(false)
    } catch {
      // Distinguished from "nothing here": an empty list is a fact about the
      // account, and showing it for a failed request invites someone to
      // re-subscribe to a part they are already watching.
      setFailed(true)
    }
  }, [user])

  useEffect(() => {
    load()
  }, [load])

  const remove = async (sub: PriceSubscription) => {
    setRemoving(sub.id)
    // Optimistic: the row goes now and comes back if the delete fails. Nothing
    // downstream depends on the moment it disappears.
    const previous = subs
    setSubs((current) => current?.filter((s) => s.id !== sub.id) ?? null)
    try {
      await cancelPriceSubscription(sub.id)
      toast.success("Price alert removed")
    } catch {
      setSubs(previous)
      toast.error("Couldn't remove the price alert")
    } finally {
      setRemoving(null)
    }
  }

  if (!user && !authLoading) return null

  return (
    <div className="max-w-2xl">
      <h3 className="text-lg font-semibold py-4">Price Alerts</h3>
      <p className="text-muted-foreground text-sm pb-4">
        Parts you're watching. We'll email you once, when the price drops.
      </p>

      {subs === null && !failed && (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-16 w-full rounded-lg" />
          <Skeleton className="h-16 w-full rounded-lg" />
        </div>
      )}

      {failed && (
        <div className="flex items-center gap-3 text-sm">
          <span className="text-muted-foreground">
            Couldn't load your price alerts.
          </span>
          <Button type="button" variant="outline" size="sm" onClick={load}>
            Try again
          </Button>
        </div>
      )}

      {subs !== null && subs.length === 0 && (
        <p className="text-muted-foreground text-sm">
          You're not watching anything yet. Open a build and use the{" "}
          <BellIcon className="inline size-4 align-text-bottom" /> beside a part
          to be told when it gets cheaper.
        </p>
      )}

      {subs !== null && subs.length > 0 && (
        <ul className="flex flex-col gap-2">
          {subs.map((sub) => (
            <li
              key={sub.id}
              className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">
                  {/* Null when the catalog row has since gone. The alert still
                      stands, so the row does too, rather than vanishing. */}
                  {sub.target_name ?? "Unavailable part"}
                </p>
                <p className="text-muted-foreground text-xs">
                  {sub.threshold_cents != null
                    ? `Alert at ${formatCents(sub.threshold_cents)}`
                    : sub.baseline_price_cents != null
                      ? `Alert on any drop below ${formatCents(sub.baseline_price_cents)}`
                      : "Alert on any drop"}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-3">
                <span className="text-sm text-muted-foreground">
                  {sub.current_price_cents != null
                    ? `Now ${formatCents(sub.current_price_cents)}`
                    : "No price yet"}
                </span>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={removing === sub.id}
                  onClick={() => remove(sub)}
                >
                  Remove
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
