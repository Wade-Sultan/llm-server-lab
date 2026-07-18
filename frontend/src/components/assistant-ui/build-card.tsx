"use client"

import type { DataMessagePartComponent } from "@assistant-ui/react"
import { ShoppingCartIcon } from "lucide-react"
import { useEffect, useState } from "react"
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
import { fetchBestListings, type Listing } from "@/lib/listings"

const formatPrice = (value: number, currency = "USD") =>
  value.toLocaleString("en-US", { style: "currency", currency })

/**
 * Fetch the current listing for each part from the commerce service. The
 * BuildData embedded in the message is a snapshot from when the build was
 * generated; live listings give historical builds current prices and working
 * affiliate URLs. Absent entries mean "use the snapshot".
 */
function usePartListings(parts: BuildData["parts"]) {
  const [listings, setListings] = useState<Record<string, Listing>>({})
  const partIdsKey = parts.map((p) => p.part_id).join(",")

  useEffect(() => {
    let cancelled = false
    fetchBestListings(partIdsKey.split(",").filter(Boolean)).then((results) => {
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
            const listing = listings[part.part_id]
            const amazonUrl = listing?.url ?? part.amazon_url
            return (
              <div
                key={part.part_id}
                className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2"
              >
                <div className="min-w-0">
                  <p className="text-muted-foreground text-xs uppercase tracking-wide">
                    {part.component}
                  </p>
                  <p className="truncate text-sm font-medium">
                    {part.brand} {part.model}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {listing?.price_amount != null && (
                    <span className="text-sm text-muted-foreground">
                      {formatPrice(
                        listing.price_amount / 100,
                        listing.currency ?? "USD",
                      )}
                    </span>
                  )}
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={!amazonUrl}
                    aria-label={`Buy ${part.brand} ${part.model} on Amazon`}
                    asChild={!!amazonUrl}
                  >
                    {amazonUrl ? (
                      <a
                        href={amazonUrl}
                        target="_blank"
                        rel="noopener noreferrer sponsored"
                      >
                        <ShoppingCartIcon className="size-4" />
                        Amazon
                      </a>
                    ) : (
                      <>
                        <ShoppingCartIcon className="size-4" />
                        Amazon
                      </>
                    )}
                  </Button>
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
