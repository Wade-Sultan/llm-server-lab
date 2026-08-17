// Client helpers for shared-build links and the PDF export.
//
// The case pick deliberately does NOT live here: it is a `select-case`
// transport command sent through the chat runtime (see case-card.tsx), not an
// HTTP call of its own.

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"

/** The public page for a shared build — same-origin, so shareable as-is. */
export function sharedBuildUrl(token: string): string {
  // window.location is fine here: every caller is a client component acting
  // on a user gesture (copy link), never during SSR.
  return `${window.location.origin}/b/${token}`
}

/** The deterministic PDF export for a shared build (backend-rendered). */
export function sharedBuildPdfUrl(token: string): string {
  return `${API_BASE}/api/v1/builds/${token}/pdf`
}

export interface SharedBuildResponse {
  token: string
  build: {
    label: string
    description: string
    total_approx: number
    parts: import("@/types/build").RecommendedPart[]
  }
  created_at: string
}

export async function fetchSharedBuild(
  token: string,
): Promise<SharedBuildResponse | null> {
  const res = await fetch(
    `${API_BASE}/api/v1/builds/${encodeURIComponent(token)}`,
  )
  if (!res.ok) return null
  return res.json()
}
