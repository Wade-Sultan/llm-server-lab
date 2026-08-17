// Browser-side helpers for the image upload routes. Kept separate from
// lib/storage.ts so client components never pull the GCS SDK into their bundle.

async function upload(endpoint: string, file: File): Promise<string> {
  const body = new FormData();
  body.append('file', file);

  const res = await fetch(endpoint, { method: 'POST', body });
  const payload = (await res.json().catch(() => null)) as { url?: string; error?: string } | null;

  if (!res.ok || !payload?.url) {
    throw new Error(payload?.error ?? `Upload failed (${res.status})`);
  }
  return payload.url;
}

export async function uploadImage(file: File): Promise<string> {
  return upload('/api/blog/upload', file);
}

export async function uploadPartImage(file: File): Promise<string> {
  return upload('/api/parts/upload', file);
}
