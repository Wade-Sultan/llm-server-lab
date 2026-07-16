'use server';

import { revalidatePath } from 'next/cache';
import { db } from '@/lib/prisma';
import { splitCommaList, slugify } from '@/lib/utils';

export interface GameMinimumPartFormData {
  tier: string; // minimum | recommended | ultra
  role: string; // cpu | gpu
  partId: string | null;
  publishedName: string;
  minRamGb: number | null;
}

export interface GameFormData {
  title: string;
  slug: string;
  genre: string;
  storeUrl: string;
  imageUrl: string;
  hardRequirementsInput: string;
  minStorageGb: number | null;
  requirementsNotes: string;
  minimumParts: GameMinimumPartFormData[];
}

function toData(d: GameFormData) {
  return {
    title: d.title,
    slug: d.slug ? slugify(d.slug) : slugify(d.title),
    genre: d.genre || null,
    storeUrl: d.storeUrl || null,
    imageUrl: d.imageUrl || null,
    hardRequirements: splitCommaList(d.hardRequirementsInput),
    minStorageGb: d.minStorageGb,
    requirementsNotes: d.requirementsNotes || null,
  };
}

// Every GameMinimumPart column is represented in the form, so children are
// simply replaced wholesale on save.
function toMinimumParts(rows: GameMinimumPartFormData[]) {
  return rows.map((r) => ({
    tier: r.tier,
    role: r.role,
    partId: r.partId,
    publishedName: r.publishedName || null,
    minRamGb: r.minRamGb,
  }));
}

export async function createGame(data: GameFormData) {
  await db.game.create({
    data: { ...toData(data), minimumParts: { create: toMinimumParts(data.minimumParts) } },
  });
  revalidatePath('/games');
}

export async function updateGame(id: string, data: GameFormData) {
  await db.game.update({
    where: { id },
    data: {
      ...toData(data),
      minimumParts: { deleteMany: {}, create: toMinimumParts(data.minimumParts) },
    },
  });
  revalidatePath('/games');
}

export async function deleteGame(id: string) {
  await db.game.delete({ where: { id } });
  revalidatePath('/games');
}
