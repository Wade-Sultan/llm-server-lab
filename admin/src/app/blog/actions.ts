'use server';

import { revalidatePath } from 'next/cache';
import { db } from '@/lib/prisma';
import { splitCommaList, slugify } from '@/lib/utils';

export interface BlogPostFormData {
  title: string;
  slug: string;
  excerpt: string;
  contentMarkdown: string;
  coverImageUrl: string;
  coverImageAlt: string;
  authorName: string;
  tagsInput: string;
  status: string; // draft | published
  isFeatured: boolean;
}

// Average adult reading speed for prose; good enough for a "N min read" badge.
const WORDS_PER_MINUTE = 225;

function readingMinutes(markdown: string): number {
  const words = markdown
    // Don't count image/link URLs or code fences toward reading time.
    .replace(/!?\[[^\]]*\]\([^)]*\)/g, ' ')
    .replace(/```[\s\S]*?```/g, ' ')
    .split(/\s+/)
    .filter(Boolean).length;
  return Math.max(1, Math.ceil(words / WORDS_PER_MINUTE));
}

function toData(d: BlogPostFormData) {
  return {
    title: d.title,
    slug: d.slug ? slugify(d.slug) : slugify(d.title),
    excerpt: d.excerpt || null,
    contentMarkdown: d.contentMarkdown,
    coverImageUrl: d.coverImageUrl || null,
    coverImageAlt: d.coverImageAlt || null,
    authorName: d.authorName || null,
    tags: splitCommaList(d.tagsInput),
    status: d.status,
    readingMinutes: readingMinutes(d.contentMarkdown),
    isFeatured: d.isFeatured,
  };
}

export async function createBlogPost(data: BlogPostFormData) {
  const base = toData(data);
  await db.blogPost.create({
    data: {
      ...base,
      // A post created straight into "published" is live as of now.
      publishedAt: base.status === 'published' ? new Date() : null,
    },
  });
  revalidatePath('/blog');
}

export async function updateBlogPost(id: string, data: BlogPostFormData) {
  const base = toData(data);
  const existing = await db.blogPost.findUnique({
    where: { id },
    select: { publishedAt: true },
  });

  // publishedAt is stamped the first time a post goes live and preserved on
  // every later edit, so fixing a typo doesn't reorder the index. Unpublishing
  // back to draft clears it, so re-publishing dates it correctly.
  const publishedAt =
    base.status === 'published' ? (existing?.publishedAt ?? new Date()) : null;

  await db.blogPost.update({ where: { id }, data: { ...base, publishedAt } });
  revalidatePath('/blog');
}

export async function deleteBlogPost(id: string) {
  await db.blogPost.delete({ where: { id } });
  revalidatePath('/blog');
}
