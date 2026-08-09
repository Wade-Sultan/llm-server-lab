import type { Metadata } from "next"

import BlogIndexPage from "@/components/pages/BlogIndexPage"
import { fetchBlogPosts } from "@/lib/blog"

// Rendered per request, not at build time: the API isn't reachable from the
// build container, so a prerendered index would bake in the empty state. The
// fetch itself is still cached (see REVALIDATE_SECONDS in lib/blog.ts), so this
// costs one upstream call every few minutes, not one per visitor.
export const dynamic = "force-dynamic"

export const metadata: Metadata = {
  title: "Blog | Palladium",
  description: "Notes on PC building, parts, and what we're shipping.",
}

export default async function Page() {
  const posts = await fetchBlogPosts()
  return <BlogIndexPage posts={posts} />
}
