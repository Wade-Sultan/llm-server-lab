export const dynamic = 'force-dynamic';

import { db } from '@/lib/prisma';
import { BlogPostsTable } from './client';

export default async function BlogPage() {
  const posts = await db.blogPost.findMany({
    // Drafts first (they're the ones needing attention), then newest.
    orderBy: [{ status: 'asc' }, { updatedAt: 'desc' }],
  });

  return <BlogPostsTable posts={posts} />;
}
