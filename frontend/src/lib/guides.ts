// Read-only access to the guide-video catalog, served by the FastAPI /guides
// routes. Managed in the admin panel; these entries are links to third-party
// videos, not hosted media.

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"

export interface GuideVideo {
  id: string
  title: string
  description: string | null
  url: string
  youtube_video_id: string | null
}

// The catalog changes only when I edit it in the admin, so cache it and
// re-check every few minutes rather than per request.
const REVALIDATE_SECONDS = 300

export async function fetchGuideVideos(): Promise<GuideVideo[]> {
  try {
    const res = await fetch(`${API_BASE}/api/v1/guides/videos`, {
      next: { revalidate: REVALIDATE_SECONDS },
    })
    if (!res.ok) return []
    const data = await res.json()
    return data.data ?? []
  } catch {
    // Non-critical page: render the empty state rather than a 500.
    return []
  }
}
