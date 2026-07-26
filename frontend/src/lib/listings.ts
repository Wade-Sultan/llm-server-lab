import { COMMERCE_BASE } from "@/lib/commerce"

export interface Listing {
  id: string
  part_id: string
  listing_type: string
  marketplace: string
  url: string | null
  price_amount: number | null
  currency: string | null
  image_url: string | null
  is_active: boolean
  asin?: string | null
  brand?: string | null
  is_prime?: boolean | null
}

async function fetchListingsForPart(partId: string): Promise<Listing[]> {
  const res = await fetch(
    `${COMMERCE_BASE}/api/v1/listings/?part_id=${encodeURIComponent(partId)}`,
  )
  if (!res.ok) return []
  const data = await res.json()
  return data.data ?? []
}

/** The current listing for a part, split by marketplace. */
export interface PartListings {
  amazon?: Listing
  ebay?: Listing
}

/**
 * Fetch the current listings for each part, keyed by part_id and split by
 * marketplace (the first active listing of each). Parts with no active listing
 * (or failed requests) are simply absent from the result — callers fall back to
 * whatever snapshot data they already have.
 */
export async function fetchListingsByPart(
  partIds: string[],
): Promise<Record<string, PartListings>> {
  const results: Record<string, PartListings> = {}
  await Promise.allSettled(
    partIds.map(async (partId) => {
      const listings = await fetchListingsForPart(partId)
      const byMarketplace: PartListings = {}
      for (const l of listings) {
        if (l.marketplace === "amazon") byMarketplace.amazon ??= l
        else if (l.marketplace === "ebay") byMarketplace.ebay ??= l
      }
      if (byMarketplace.amazon || byMarketplace.ebay) {
        results[partId] = byMarketplace
      }
    }),
  )
  return results
}
