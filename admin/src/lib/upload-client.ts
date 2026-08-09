// Browser-side helper for the blog image upload route. Kept separate from
// lib/storage.ts so client components never pull the GCS SDK into their bundle.

export async function uploadImage(file: File): Promise<string> {
  const body = new FormData();
  body.append('file', file);

  const res = await fetch('/api/blog/upload', { method: 'POST', body });
  const payload = (await res.json().catch(() => null)) as { url?: string; error?: string } | null;

  if (!res.ok || !payload?.url) {
    throw new Error(payload?.error ?? `Upload failed (${res.status})`);
  }
  return payload.url;
}
