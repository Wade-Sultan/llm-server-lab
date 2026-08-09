// Read-only access to the published blog, served by the FastAPI /blog routes.
// Posts are authored in the admin panel; these endpoints are public and only
// ever return published posts.

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"

export interface BlogPostSummary {
  id: string
  slug: string
  title: string
  excerpt: string | null
  cover_image_url: string | null
  cover_image_alt: string | null
  author_name: string | null
  tags: string[]
  published_at: string | null
  reading_minutes: number | null
  is_featured: boolean
}

export interface BlogPostDetail extends BlogPostSummary {
  content_markdown: string
}

// Posts change only when I publish, so serve them from cache and re-check
// every few minutes rather than hitting the API on every request.
const REVALIDATE_SECONDS = 300

export async function fetchBlogPosts(limit = 50): Promise<BlogPostSummary[]> {
  try {
    const res = await fetch(`${API_BASE}/api/v1/blog/posts?limit=${limit}`, {
      next: { revalidate: REVALIDATE_SECONDS },
    })
    if (!res.ok) return []
    const data = await res.json()
    return data.data ?? []
  } catch {
    // The blog is non-critical: render an empty index rather than a 500.
    return []
  }
}

export async function fetchBlogPost(
  slug: string,
): Promise<BlogPostDetail | null> {
  try {
    const res = await fetch(
      `${API_BASE}/api/v1/blog/posts/${encodeURIComponent(slug)}`,
      { next: { revalidate: REVALIDATE_SECONDS } },
    )
    if (!res.ok) return null
    return await res.json()
  } catch {
    return null
  }
}

export function formatPublishedDate(value: string | null): string {
  if (!value) return ""
  return new Date(value).toLocaleDateString("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
  })
}
