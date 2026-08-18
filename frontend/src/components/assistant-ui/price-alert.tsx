"use client"

import { BellIcon, BellRingIcon } from "lucide-react"
import Link from "next/link"
import { useEffect, useState } from "react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { LoadingButton } from "@/components/ui/loading-button"
import useAuth from "@/hooks/useAuth"
import {
  cancelPriceSubscription,
  createPriceSubscription,
  lookupPriceTargets,
  type PriceSubscription,
  type PriceTarget,
} from "@/lib/priceAlerts"
import { cn, formatCents } from "@/lib/utils"

/**
 * "Tell me when this gets cheaper", per part.
 *
 * The bell sits outside each part row's box (see build-card.tsx) so it reads as
 * an action on the part rather than a third place to buy it. It rings on hover
 * — .bell-btn in index.css, which is also where the reduced-motion opt-out
 * lives — and switches to a struck bell once the part is being watched, so the
 * card shows existing alerts without being opened.
 */

/**
 * Resolve a card's parts to their price targets in one request, keyed by
 * part_id. Absent entries mean "nothing watchable here" and the card renders no
 * bell — see lookupPriceTargets.
 *
 * The setter is returned alongside so the dialog can write back the
 * subscription it just created or canceled; re-fetching the whole card to
 * learn something we were just told would be a round trip for nothing.
 */
export function usePriceTargets(partIds: string[]) {
  const { user, loading: authLoading } = useAuth()
  const [targets, setTargets] = useState<Record<string, PriceTarget>>({})

  // The session is part of the key, not just the part list: `subscription` is
  // only filled in for an authenticated request, so a lookup fired before
  // Firebase has resolved the session comes back with every bell unwatched.
  // Null until it has resolved, which holds the request rather than spending it
  // on an answer that would be wrong; whoever is signed in when it settles is
  // what the effect keys on, so signing out re-asks as a guest.
  const lookupKey = authLoading
    ? null
    : `${user?.uid ?? "guest"}|${partIds.filter(Boolean).join(",")}`

  useEffect(() => {
    if (lookupKey === null) return
    let cancelled = false
    const ids = lookupKey.split("|")[1].split(",").filter(Boolean)
    lookupPriceTargets(ids).then((results) => {
      if (!cancelled) setTargets(results)
    })
    return () => {
      cancelled = true
    }
  }, [lookupKey])

  const setSubscription = (partId: string, sub: PriceSubscription | null) =>
    setTargets((prev) => {
      const target = prev[partId]
      if (!target) return prev
      return { ...prev, [partId]: { ...target, subscription: sub } }
    })

  return { targets, setSubscription }
}

/** Dollars as typed into the threshold field, in cents. Null when unusable. */
function parseDollars(input: string): number | null {
  const trimmed = input.trim().replace(/^\$/, "").replace(/,/g, "")
  if (!trimmed) return null
  const value = Number(trimmed)
  if (!Number.isFinite(value) || value <= 0) return null
  // Rounded rather than truncated: 99.999 typed into a price field means the
  // dollar, not 99.99.
  return Math.round(value * 100)
}

export function PriceAlertBell({
  target,
  partLabel,
  onSubscriptionChange,
}: {
  target: PriceTarget
  partLabel: string
  onSubscriptionChange: (sub: PriceSubscription | null) => void
}) {
  const { user, loading: authLoading } = useAuth()
  const [open, setOpen] = useState(false)
  const [threshold, setThreshold] = useState("")
  const [saving, setSaving] = useState(false)
  const [canceling, setCanceling] = useState(false)

  const subscription = target.subscription
  const currentCents = target.current_price_cents

  // Seed the field each time the dialog opens: with the threshold already set
  // if this is an edit, and empty otherwise so the placeholder can offer "any
  // drop" as the default rather than a number the user has to clear.
  useEffect(() => {
    if (!open) return
    setThreshold(
      subscription?.threshold_cents != null
        ? (subscription.threshold_cents / 100).toFixed(2)
        : "",
    )
  }, [open, subscription?.threshold_cents])

  const thresholdCents = parseDollars(threshold)
  const invalid = threshold.trim() !== "" && thresholdCents === null
  // A threshold at or above today's price isn't wrong — it still only fires on
  // an actual drop — but it is almost always a typo, so say what it will do.
  const aboveCurrent =
    thresholdCents !== null &&
    currentCents !== null &&
    thresholdCents >= currentCents

  const save = async () => {
    if (invalid || saving) return
    setSaving(true)
    try {
      const sub = await createPriceSubscription({
        targetKind: target.target_kind,
        targetId: target.target_id,
        thresholdCents,
      })
      onSubscriptionChange(sub)
      setOpen(false)
      toast.success(
        thresholdCents === null
          ? `We'll email you when ${partLabel} drops in price`
          : `We'll email you when ${partLabel} reaches ${formatCents(thresholdCents)}`,
      )
    } catch {
      toast.error("Couldn't save your price alert")
    } finally {
      setSaving(false)
    }
  }

  const stop = async () => {
    if (!subscription || canceling) return
    setCanceling(true)
    try {
      await cancelPriceSubscription(subscription.id)
      onSubscriptionChange(null)
      setOpen(false)
      toast.success("Price alert removed")
    } catch {
      toast.error("Couldn't remove the price alert")
    } finally {
      setCanceling(false)
    }
  }

  const watching = subscription != null
  const Icon = watching ? BellRingIcon : BellIcon

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        className={cn("bell-btn shrink-0", watching && "text-primary")}
        title={watching ? "Edit price alert" : "Get a price alert"}
        aria-label={
          watching
            ? `Edit price alert for ${partLabel}`
            : `Get a price alert for ${partLabel}`
        }
        onClick={() => setOpen(true)}
      >
        <Icon className="bell-btn-icon size-4" />
      </Button>

      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Price Alert</DialogTitle>
          <DialogDescription>{partLabel}</DialogDescription>
        </DialogHeader>

        <div className="flex items-baseline justify-between rounded-lg border px-3 py-2">
          <span className="text-muted-foreground text-sm">Current price</span>
          <span className="font-medium">
            {currentCents !== null
              ? formatCents(currentCents)
              : "Not priced yet"}
          </span>
        </div>

        {/* Guests get the same dialog rather than a disabled bell: the price is
            worth showing, and the ask is clearer once they can see it. */}
        {!user && !authLoading ? (
          <p className="text-muted-foreground text-sm">
            <Link href="/login" className="underline underline-offset-4">
              Sign in
            </Link>{" "}
            to be emailed when this part gets cheaper.
          </p>
        ) : (
          <div className="flex flex-col gap-2">
            <Label htmlFor="price-alert-threshold">
              Email me when it drops to
            </Label>
            <div className="relative">
              <span className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-muted-foreground text-sm">
                $
              </span>
              <Input
                id="price-alert-threshold"
                inputMode="decimal"
                autoComplete="off"
                className="pl-7"
                placeholder="Any drop"
                value={threshold}
                onChange={(e) => setThreshold(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") save()
                }}
                aria-invalid={invalid}
              />
            </div>
            <p className="text-muted-foreground text-xs">
              {invalid
                ? "Enter a price like 249.99."
                : aboveCurrent
                  ? "That's at or above today's price — you'll hear from us on the next drop."
                  : thresholdCents === null
                    ? "Leave blank to hear about any drop from today's price."
                    : `We'll email you once it reaches ${formatCents(thresholdCents)}.`}
            </p>
            {target.active_count > 0 && (
              <p className="text-muted-foreground text-xs">
                {target.active_count === 1
                  ? "1 person is watching this part."
                  : `${target.active_count} people are watching this part.`}
              </p>
            )}
          </div>
        )}

        <DialogFooter>
          {watching && (
            <Button
              type="button"
              variant="ghost"
              className="text-destructive sm:mr-auto"
              disabled={canceling}
              onClick={stop}
            >
              Stop watching
            </Button>
          )}
          <DialogClose asChild>
            <Button type="button" variant="outline">
              Cancel
            </Button>
          </DialogClose>
          {(user || authLoading) && (
            <LoadingButton
              type="button"
              loading={saving}
              disabled={invalid}
              onClick={save}
            >
              {watching ? "Update alert" : "Create alert"}
            </LoadingButton>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
