// Base URL + account calls for the Go commerce service. Falls back to the
// FastAPI URL so local dev and rollback work without the extra env var.

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"
export const COMMERCE_BASE = process.env.NEXT_PUBLIC_COMMERCE_URL ?? API_BASE

/**
 * Create or link the Postgres users row for the signed-in Firebase user.
 * Fire-and-forget: chat auto-provisions rows too, so a failed sync here is
 * recoverable and must never block the auth flow.
 */
export async function syncAccount(token: string): Promise<void> {
  try {
    await fetch(`${COMMERCE_BASE}/api/v1/account/sync`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    })
  } catch {
    // Non-fatal; see above.
  }
}

/** Delete the signed-in user's account. Throws with a readable message. */
export async function deleteAccount(token: string): Promise<void> {
  const res = await fetch(`${COMMERCE_BASE}/api/v1/account`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.error || "Failed to delete account")
  }
}
