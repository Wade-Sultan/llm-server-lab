import type { Metadata } from "next"
import { notFound } from "next/navigation"

import BlogPostPage from "@/components/pages/BlogPostPage"
import { fetchBlogPost } from "@/lib/blog"

type Props = { params: Promise<{ slug: string }> }

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params
  const post = await fetchBlogPost(slug)
  if (!post) return { title: "Post not found | Palladium" }

  return {
    title: `${post.title} | Palladium`,
    description: post.excerpt ?? undefined,
    openGraph: {
      type: "article",
      title: post.title,
      description: post.excerpt ?? undefined,
      publishedTime: post.published_at ?? undefined,
      images: post.cover_image_url ? [post.cover_image_url] : undefined,
    },
  }
}

export default async function Page({ params }: Props) {
  const { slug } = await params
  const post = await fetchBlogPost(slug)
  if (!post) notFound()

  return <BlogPostPage post={post} />
}
