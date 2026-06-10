const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ""

export async function wakeUpBuilder(): Promise<void> {
  if (!API_URL) return

  try {
    await fetch(`${API_URL}/api/v1/health`, {
      cache: "no-store",
    })
  } catch {
    // Silently ignore — this is best-effort.
  }
}
