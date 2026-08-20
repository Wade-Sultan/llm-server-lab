"use client"

import type { DataMessagePartComponent } from "@assistant-ui/react"
import {
  FileDownIcon,
  LinkIcon,
  ThumbsDownIcon,
  ThumbsUpIcon,
} from "lucide-react"
import { useEffect, useState } from "react"
import type { IconType } from "react-icons"
import { FaAmazon, FaEbay } from "react-icons/fa6"
import { toast } from "sonner"
import {
  PriceAlertBell,
  usePriceTargets,
} from "@/components/assistant-ui/price-alert"
import { useChatConversation } from "@/components/Chat/chatruntimeprovider"
import { AffiliateDisclosure } from "@/components/Common/AffiliateDisclosure"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  clearBuildFeedback,
  FeedbackError,
  type FeedbackRating,
  setBuildFeedback,
} from "@/lib/feedback"
import { fetchListingsByPart, type PartListings } from "@/lib/listings"
import { sharedBuildPdfUrl, sharedBuildUrl } from "@/lib/share"
import { cn, formatCents } from "@/lib/utils"
import type { BuildData } from "@/types/build"

/**
 * Thumbs up/down on this build.
 *
 * Optimistic, and deliberately forgiving about failure: the rating is written
 * to local state immediately and rolled back if the request fails. Losing a
 * thumb is not worth a blocking spinner on a card the user is reading, and the
 * rating carries no consequence they are waiting on.
 *
 * Clicking the lit thumb clears the rating rather than re-sending it — "rated
 * then changed their mind" and "never rated" are the same thing to every
 * question this data answers, so the row is deleted rather than neutralised.
 */
function BuildFeedback({ buildKey }: { buildKey: string }) {
  const conversation = useChatConversation()
  const [rating, setRating] = useState<FeedbackRating | null>(
    conversation?.initialFeedback ?? null,
  )

  // No conversation context means this card is not inside a chat (build
  // history renders the same component), and there is nothing to rate against.
  if (!conversation) return null

  const vote = async (next: FeedbackRating) => {
    const previous = rating
    const clearing = previous === next
    setRating(clearing ? null : next)
    try {
      if (clearing) {
        await clearBuildFeedback(conversation.id)
      } else {
        await setBuildFeedback(conversation.id, next, buildKey)
      }
    } catch (error) {
      setRating(previous)
      // Named causes rather than one shrug. A 404 here is the ordinary race —
      // the chat is saved when the turn finishes, and the build card appears
      // before that — and telling someone to wait a moment is actionable where
      // "could not save" is not. 401 means the token lapsed mid-conversation,
      // which a reload fixes. Anything else is ours, not theirs.
      const status = error instanceof FeedbackError ? error.status : 0
      if (status === 404) {
        toast.error("This chat is still saving — try again in a moment")
      } else if (status === 401 || status === 403) {
        toast.error("Your session expired — reload and try again")
      } else {
        toast.error("Could not save your feedback")
      }
      // The status alone does not say which of the 404s it was, and a 500 is
      // almost always a database that has not run the build_feedback migration.
      console.error("build feedback failed", error)
    }
  }

  return (
    <div className="flex shrink-0 items-center gap-1">
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        aria-label="Good build"
        aria-pressed={rating === "up"}
        onClick={() => vote("up")}
      >
        <ThumbsUpIcon
          className={cn("size-4", rating === "up" && "fill-current")}
        />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        aria-label="Bad build"
        aria-pressed={rating === "down"}
        onClick={() => vote("down")}
      >
        <ThumbsDownIcon
          className={cn("size-4", rating === "down" && "fill-current")}
        />
      </Button>
    </div>
  )
}

/**
 * A single marketplace buy button — the brand's icon as a link to the
 * marketplace. Renders as a disabled button when no url is available so the
 * marketplace stays visible. `className` carries the per-brand hover-border
 * effect (see .mp-btn-* in index.css).
 */
function MarketplaceButton({
  url,
  label,
  icon: Icon,
  className,
  partLabel,
}: {
  url: string | null | undefined
  label: string
  icon: IconType
  className?: string
  partLabel: string
}) {
  return (
    <Button
      type="button"
      variant="outline"
      size="icon-sm"
      className={className}
      disabled={!url}
      title={`Buy on ${label}`}
      aria-label={`Buy ${partLabel} on ${label}`}
      asChild={!!url}
    >
      {url ? (
        <a href={url} target="_blank" rel="noopener noreferrer sponsored">
          <Icon className="size-4" />
        </a>
      ) : (
        <Icon className="size-4" />
      )}
    </Button>
  )
}

/**
 * Fetch the current listing for each part from the commerce service. The
 * BuildData embedded in the message is a snapshot from when the build was
 * generated; live listings give historical builds current prices and working
 * affiliate URLs. Absent entries mean "use the snapshot".
 */
function usePartListings(parts: BuildData["parts"]) {
  const [listings, setListings] = useState<Record<string, PartListings>>({})
  const partIdsKey = parts.map((p) => p.part_id).join(",")

  useEffect(() => {
    let cancelled = false
    fetchListingsByPart(partIdsKey.split(",").filter(Boolean)).then(
      (results) => {
        if (!cancelled) setListings(results)
      },
    )
    return () => {
      cancelled = true
    }
  }, [partIdsKey])

  return listings
}

/**
 * Copy-link and PDF actions for a build that has a public snapshot. Both are
 * addressed by the share token; builds generated before the snapshot feature
 * (and turns whose snapshot write failed) have none, and render no actions
 * rather than dead buttons.
 */
function ShareActions({ shareToken }: { shareToken: string }) {
  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(sharedBuildUrl(shareToken))
      toast.success("Build link copied")
    } catch {
      toast.error("Couldn't copy the link")
    }
  }

  return (
    <div className="flex shrink-0 items-center gap-1">
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        aria-label="Copy link to this build"
        title="Copy link to this build"
        onClick={copyLink}
      >
        <LinkIcon className="size-4" />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        aria-label="Download this build as PDF"
        title="Download this build as PDF"
        asChild
      >
        <a href={sharedBuildPdfUrl(shareToken)} download>
          <FileDownIcon className="size-4" />
        </a>
      </Button>
    </div>
  )
}

export const BuildCard: DataMessagePartComponent<BuildData> = (props) => {
  const data = props.data as BuildData
  const listings = usePartListings(data.parts)
  // One request for the whole card: which parts can be watched, what the
  // catalog says they cost, and which the signed-in user already watches.
  const { targets, setSubscription } = usePriceTargets(
    data.parts.map((p) => p.part_id),
  )

  return (
    <div className="my-2 flex w-full max-w-(--thread-max-width) flex-col gap-1">
      <Card className="aui-build-card w-full">
        <CardHeader>
          <div className="flex items-center justify-between gap-2">
            <CardTitle className="text-base">{data.label}</CardTitle>
            <Badge variant="secondary">
              ~{formatCents(data.total_approx)}*
            </Badge>
          </div>
          <CardDescription>{data.description}</CardDescription>
          {/* Above the parts list, not in the footer: the disclosure has to be
              readable before the first buy button, not after it. See
              AffiliateDisclosure for the rule this satisfies. */}
          <AffiliateDisclosure className="mt-1" />
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          {data.parts.map((part) => {
            const partListings = listings[part.part_id]
            const amazonListing = partListings?.amazon
            const ebayListing = partListings?.ebay
            const amazonUrl = amazonListing?.url ?? part.amazon_url
            const ebayUrl = ebayListing?.url
            // Live price comes from the Amazon listing (eBay listings are
            // filtered search links with no single price), falling back to the
            // catalog price captured when the build was generated. The fallback
            // matters more than it looks: a part with no Amazon listing used to
            // render with no price at all beside a disabled buy button, which
            // reads as "this part is free" rather than "we have no listing" —
            // and it hid four-figure catalog prices that the header total was
            // meanwhile including.
            const priceListing = amazonListing
            const unitPriceCents =
              priceListing?.price_amount ?? part.approx_price
            const priceCurrency =
              priceListing?.price_amount != null
                ? (priceListing.currency ?? "USD")
                : "USD"
            const partLabel = `${part.brand} ${part.model}`
            // A build can hold several of a part (four matched GPUs, three
            // fans). Absent on reference builds, which predate the field.
            const quantity = part.quantity ?? 1
            const priceTarget = targets[part.part_id]
            return (
              <div
                // part_id is "" for a name the catalog couldn't resolve, so it
                // is not unique on its own once a build can carry several rows
                // in one role — pair it with the component and model.
                key={`${part.component}:${part.part_id || part.model}`}
                className="flex items-center gap-1"
              >
                <div className="flex min-w-0 flex-1 items-center justify-between gap-3 rounded-lg border px-3 py-2">
                  <div className="min-w-0">
                    <p className="text-muted-foreground text-xs uppercase tracking-wide">
                      {part.component}
                    </p>
                    <p className="truncate text-sm font-medium">
                      {quantity > 1 && (
                        <span className="text-muted-foreground">
                          {quantity}×{" "}
                        </span>
                      )}
                      {part.brand} {part.model}
                    </p>
                  </div>
                  <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
                    {unitPriceCents != null && (
                      <span className="text-sm text-muted-foreground">
                        {/* Line total: both price sources are per unit. */}
                        {formatCents(unitPriceCents * quantity, priceCurrency)}
                      </span>
                    )}
                    <MarketplaceButton
                      url={amazonUrl}
                      label="Amazon"
                      icon={FaAmazon}
                      className="mp-btn mp-btn-amazon"
                      partLabel={partLabel}
                    />
                    <MarketplaceButton
                      url={ebayUrl}
                      label="eBay"
                      icon={FaEbay}
                      className="mp-btn mp-btn-ebay"
                      partLabel={partLabel}
                    />
                  </div>
                </div>
                {/* Outside the part's box, inside the card: watching a price is
                    an action on the part, not a third place to buy it. The slot
                    is always there so every row's box ends at the same place —
                    a part with nothing watchable (see lookupPriceTargets) shows
                    no bell rather than a dead one, and a card mixing the two
                    would otherwise have ragged edges. */}
                <div className="flex w-8 shrink-0 items-center justify-center">
                  {priceTarget && (
                    <PriceAlertBell
                      target={priceTarget}
                      partLabel={partLabel}
                      onSubscriptionChange={(sub) =>
                        setSubscription(part.part_id, sub)
                      }
                    />
                  )}
                </div>
              </div>
            )
          })}
        </CardContent>
      </Card>
      <div className="flex items-center justify-between gap-2 px-1">
        <p className="text-muted-foreground text-xs">
          *Based on approximate prices derived from Google Shopping
        </p>
        <div className="flex items-center gap-1">
          {data.share_token && <ShareActions shareToken={data.share_token} />}
          <BuildFeedback buildKey={data.key} />
        </div>
      </div>
    </div>
  )
}
