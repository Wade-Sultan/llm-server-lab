import { ArrowLeft } from "lucide-react"
import Link from "next/link"
import type { Components } from "react-markdown"
import Markdown from "react-markdown"
import remarkGfm from "remark-gfm"

import { type BlogPostDetail, formatPublishedDate } from "@/lib/blog"

// react-markdown does not render raw HTML unless rehype-raw is added, so the
// admin-authored Markdown is rendered as text-only structure — no sanitiser
// needed here.
const components: Components = {
  h1: ({ children }) => (
    <h2 className="mt-10 mb-3 font-semibold text-xl tracking-tight">
      {children}
    </h2>
  ),
  h2: ({ children }) => (
    <h2 className="mt-10 mb-3 font-semibold text-xl tracking-tight">
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 className="mt-8 mb-2 font-semibold text-base tracking-tight">
      {children}
    </h3>
  ),
  p: ({ children }) => (
    <p className="my-4 text-sm leading-7 text-foreground/90">{children}</p>
  ),
  ul: ({ children }) => (
    <ul className="my-4 list-disc space-y-1.5 pl-6 text-sm leading-7 text-foreground/90">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="my-4 list-decimal space-y-1.5 pl-6 text-sm leading-7 text-foreground/90">
      {children}
    </ol>
  ),
  a: ({ href, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-blue-400 underline underline-offset-2 hover:text-blue-300"
    >
      {children}
    </a>
  ),
  blockquote: ({ children }) => (
    <blockquote className="my-5 border-l-2 pl-4 text-sm text-muted-foreground italic">
      {children}
    </blockquote>
  ),
  code: ({ className, children }) => {
    // Fenced blocks get a language class; inline code does not.
    const isBlock = Boolean(className)
    if (isBlock) {
      return (
        <code className="block overflow-x-auto rounded-lg border bg-muted/50 p-4 font-mono text-xs leading-6">
          {children}
        </code>
      )
    }
    return (
      <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
        {children}
      </code>
    )
  },
  pre: ({ children }) => <pre className="my-5">{children}</pre>,
  hr: () => <hr className="my-8" />,
  img: ({ src, alt }) => (
    // Plain <img>: images come from the media bucket, whose host isn't in
    // next.config's remotePatterns.
    <img
      src={typeof src === "string" ? src : undefined}
      alt={alt ?? ""}
      className="my-6 w-full rounded-lg border"
      loading="lazy"
    />
  ),
  table: ({ children }) => (
    <div className="my-5 overflow-x-auto">
      <table className="w-full text-sm">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border-b px-3 py-2 text-left font-medium">{children}</th>
  ),
  td: ({ children }) => (
    <td className="border-b px-3 py-2 text-foreground/90">{children}</td>
  ),
}

export default function BlogPostPage({ post }: { post: BlogPostDetail }) {
  const meta = [
    formatPublishedDate(post.published_at),
    post.author_name,
    post.reading_minutes ? `${post.reading_minutes} min read` : null,
  ]
    .filter(Boolean)
    .join(" · ")

  return (
    <div className="h-full overflow-y-auto">
      <article className="mx-auto max-w-2xl px-6 py-10">
        <Link
          href="/blog"
          className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-3.5" />
          All posts
        </Link>

        <header className="mt-6 mb-8">
          <h1 className="text-2xl font-semibold tracking-tight">
            {post.title}
          </h1>
          {meta && <p className="mt-2 text-xs text-muted-foreground">{meta}</p>}
          {post.tags.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {post.tags.map((tag) => (
                <span
                  key={tag}
                  className="rounded-full border px-2 py-0.5 text-xs text-muted-foreground"
                >
                  {tag}
                </span>
              ))}
            </div>
          )}
          {post.cover_image_url && (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={post.cover_image_url}
              alt={post.cover_image_alt ?? ""}
              className="mt-6 w-full rounded-lg border object-cover"
            />
          )}
        </header>

        <Markdown remarkPlugins={[remarkGfm]} components={components}>
          {post.content_markdown}
        </Markdown>
      </article>
    </div>
  )
}
