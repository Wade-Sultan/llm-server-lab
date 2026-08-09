'use server';

import { revalidatePath } from 'next/cache';
import { db } from '@/lib/prisma';
import { extractYouTubeId } from '@/lib/utils';

export interface GuideVideoFormData {
  title: string;
  description: string;
  url: string;
  isPublished: boolean;
  sortOrder: number;
}

function toData(d: GuideVideoFormData) {
  const url = d.url.trim();
  return {
    title: d.title,
    description: d.description || null,
    url,
    // Re-derived on every save so fixing a bad link fixes the embed too.
    youtubeVideoId: extractYouTubeId(url),
    isPublished: d.isPublished,
    sortOrder: d.sortOrder,
  };
}

export async function createGuideVideo(data: GuideVideoFormData) {
  await db.guideVideo.create({ data: toData(data) });
  revalidatePath('/guide-videos');
}

export async function updateGuideVideo(id: string, data: GuideVideoFormData) {
  await db.guideVideo.update({ where: { id }, data: toData(data) });
  revalidatePath('/guide-videos');
}

export async function deleteGuideVideo(id: string) {
  await db.guideVideo.delete({ where: { id } });
  revalidatePath('/guide-videos');
}
