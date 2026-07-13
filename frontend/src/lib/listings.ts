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

/**
 * Fetch the best current listing for each part, keyed by part_id. Parts with
 * no active listing (or failed requests) are simply absent from the result —
 * callers fall back to whatever snapshot data they already have.
 */
export async function fetchBestListings(
  partIds: string[],
): Promise<Record<string, Listing>> {
  const results: Record<string, Listing> = {}
  await Promise.allSettled(
    partIds.map(async (partId) => {
      const listings = await fetchListingsForPart(partId)
      const best = listings.find((l) => l.url) ?? listings[0]
      if (best) results[partId] = best
    }),
  )
  return results
}
