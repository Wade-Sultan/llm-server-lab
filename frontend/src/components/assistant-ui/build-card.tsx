"use client"

import type { DataMessagePartComponent } from "@assistant-ui/react"
import { useEffect, useState } from "react"
import { type IconType } from "react-icons"
import { FaAmazon, FaEbay } from "react-icons/fa6"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import type { BuildData } from "@/hooks/useConversationState"
import { fetchListingsByPart, type PartListings } from "@/lib/listings"

const formatPrice = (value: number, currency = "USD") =>
  value.toLocaleString("en-US", { style: "currency", currency })

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
    fetchListingsByPart(partIdsKey.split(",").filter(Boolean)).then((results) => {
      if (!cancelled) setListings(results)
    })
    return () => {
      cancelled = true
    }
  }, [partIdsKey])

  return listings
}

export const BuildCard: DataMessagePartComponent<BuildData> = (props) => {
  const data = props.data as BuildData
  const listings = usePartListings(data.parts)

  return (
    <div className="my-2 flex w-full max-w-(--thread-max-width) flex-col gap-1">
      <Card className="aui-build-card w-full">
        <CardHeader>
          <div className="flex items-center justify-between gap-2">
            <CardTitle className="text-base">{data.label}</CardTitle>
            <Badge variant="secondary">
              ~{formatPrice(data.total_approx / 100)}*
            </Badge>
          </div>
          <CardDescription>{data.description}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          {data.parts.map((part) => {
            const partListings = listings[part.part_id]
            const amazonListing = partListings?.amazon
            const ebayListing = partListings?.ebay
            const amazonUrl = amazonListing?.url ?? part.amazon_url
            const ebayUrl = ebayListing?.url
            // Live price comes from the Amazon listing (eBay listings are
            // filtered search links with no single price).
            const priceListing = amazonListing
            const partLabel = `${part.brand} ${part.model}`
            // A build can hold several of a part (four matched GPUs, three
            // fans). Absent on reference builds, which predate the field.
            const quantity = part.quantity ?? 1
            return (
              <div
                // part_id is "" for a name the catalog couldn't resolve, so it
                // is not unique on its own once a build can carry several rows
                // in one role — pair it with the component and model.
                key={`${part.component}:${part.part_id || part.model}`}
                className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2"
              >
                <div className="min-w-0">
                  <p className="text-muted-foreground text-xs uppercase tracking-wide">
                    {part.component}
                  </p>
                  <p className="truncate text-sm font-medium">
                    {quantity > 1 && (
                      <span className="text-muted-foreground">{quantity}× </span>
                    )}
                    {part.brand} {part.model}
                  </p>
                </div>
                <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
                  {priceListing?.price_amount != null && (
                    <span className="text-sm text-muted-foreground">
                      {/* Line total: the listing price is per unit. */}
                      {formatPrice(
                        (priceListing.price_amount * quantity) / 100,
                        priceListing.currency ?? "USD",
                      )}
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
            )
          })}
        </CardContent>
      </Card>
      <p className="px-1 text-muted-foreground text-xs">
        *Based on approximate prices derived from Google Shopping
      </p>
    </div>
  )
}
