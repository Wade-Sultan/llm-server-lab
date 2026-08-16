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

/** A failed feedback write, carrying the status so the UI can explain itself. */
export class FeedbackError extends Error {
  readonly status: number

  constructor(status: number, body: string) {
    super(`feedback failed: ${status} ${body}`.trim())
    this.name = "FeedbackError"
    this.status = status
  }
}

async function failed(res: Response): Promise<FeedbackError> {
  // Body included because the two interesting failures are indistinguishable
  // from the status alone: 404 is both "no such conversation" and "not yours"
  // (deliberately — see the route), and a 500 here is nearly always the
  // build_feedback table not existing yet on that database.
  const body = await res.text().catch(() => "")
  return new FeedbackError(res.status, body.slice(0, 200))
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
  if (!res.ok) throw await failed(res)
}

/** Withdraw a rating — what clicking an already-lit thumb does. */
export async function clearBuildFeedback(
  conversationId: string,
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/api/v1/conversations/${conversationId}/feedback`,
    { method: "DELETE", headers: await authHeaders() },
  )
  if (!res.ok) throw await failed(res)
}
