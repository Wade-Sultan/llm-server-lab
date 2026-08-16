import { getAccessToken } from "@/hooks/useAuth"

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"

export type FeedbackRating = "up" | "down"

/**
 * The thumbs up/down a user has left on a conversation's recommended build.
 *
 * One per conversation per user, so this is a single value rather than a list —
 * clicking the lit thumb clears it, and clicking the other one switches it. The
 * server enforces the same shape with a unique constraint, so a double click
 * cannot leave two rows behind.
 */
async function authHeaders(): Promise<Record<string, string>> {
  const token = await getAccessToken()
  return token
    ? { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }
    : { "Content-Type": "application/json" }
}

/**
 * Record or change a rating.
 *
 * `buildKey` is the key off the BuildCard rather than a database id: the client
 * never sees pc_builds ids, and the server resolving the key itself is also what
 * stops a caller naming an arbitrary build row.
 */
export async function setBuildFeedback(
  conversationId: string,
  rating: FeedbackRating,
  buildKey: string | null,
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/api/v1/conversations/${conversationId}/feedback`,
    {
      method: "PUT",
      headers: await authHeaders(),
      body: JSON.stringify({ rating, build_key: buildKey }),
    },
  )
  if (!res.ok) throw new Error(`feedback failed: ${res.status}`)
}

/** Withdraw a rating — what clicking an already-lit thumb does. */
export async function clearBuildFeedback(
  conversationId: string,
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/api/v1/conversations/${conversationId}/feedback`,
    { method: "DELETE", headers: await authHeaders() },
  )
  if (!res.ok) throw new Error(`feedback failed: ${res.status}`)
}
