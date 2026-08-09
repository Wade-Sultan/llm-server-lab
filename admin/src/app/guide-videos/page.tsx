export const dynamic = 'force-dynamic';

import { db } from '@/lib/prisma';
import { GuideVideosTable } from './client';

export default async function GuideVideosPage() {
  const videos = await db.guideVideo.findMany({
    // Same order the public page uses, so the admin list reads as the grid.
    orderBy: [{ sortOrder: 'asc' }, { createdAt: 'asc' }],
  });

  return <GuideVideosTable videos={videos} />;
}
