import Link from "next/link"

import { type BlogPostSummary, formatPublishedDate } from "@/lib/blog"

function PostCard({ post }: { post: BlogPostSummary }) {
  return (
    <Link
      href={`/blog/${post.slug}`}
      className="group flex gap-5 rounded-lg border p-4 transition-colors hover:bg-accent/40"
    >
      {post.cover_image_url && (
        // Plain <img>: covers come from the media bucket, whose host isn't in
        // next.config's remotePatterns.
        <img
          src={post.cover_image_url}
          alt={post.cover_image_alt ?? ""}
          className="hidden h-24 w-36 shrink-0 rounded-md object-cover sm:block"
          loading="lazy"
        />
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-3">
          <h2 className="truncate font-medium tracking-tight group-hover:underline">
            {post.title}
          </h2>
          {post.is_featured && (
            <span className="shrink-0 rounded-full border border-blue-500/30 bg-blue-500/15 px-2 py-0.5 text-xs text-blue-400">
              Featured
            </span>
          )}
        </div>
        {post.excerpt && (
          <p className="mt-1.5 line-clamp-2 text-sm text-muted-foreground">
            {post.excerpt}
          </p>
        )}
        <p className="mt-2 text-xs text-muted-foreground">
          {[
            formatPublishedDate(post.published_at),
            post.author_name,
            post.reading_minutes ? `${post.reading_minutes} min read` : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </p>
      </div>
    </Link>
  )
}

export default function BlogIndexPage({ posts }: { posts: BlogPostSummary[] }) {
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-2xl px-6 py-10">
        <div className="mb-8">
          <h1 className="text-2xl font-semibold tracking-tight">Blog</h1>
          <p className="mt-1.5 text-sm text-muted-foreground">
            Notes on PC building, parts, and what we're shipping
          </p>
        </div>

        {posts.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No posts yet — check back soon.
          </p>
        ) : (
          <div className="space-y-4">
            {posts.map((post) => (
              <PostCard key={post.id} post={post} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
