'use server';

import { revalidatePath } from 'next/cache';
import { db } from '@/lib/prisma';
import { usdToCents } from '@/lib/utils';

export interface PsuFormData {
  name: string;
  manufacturer: string;
  modelNumber: string;
  yearReleased: number | null;
  isActive: boolean;
  streetPriceUsd: number | null;
  psuGroupId: string;
  depthMm: number | null;
}

const partData = (d: PsuFormData) => ({
  name: d.name, manufacturer: d.manufacturer || null,
  modelNumber: d.modelNumber || null, yearReleased: d.yearReleased, isActive: d.isActive,
  streetPriceCents: usdToCents(d.streetPriceUsd),
});

const specData = (d: PsuFormData) => ({ psuGroupId: d.psuGroupId, depthMm: d.depthMm });

export async function createPsu(data: PsuFormData) {
  await db.pcPart.create({ data: { ...partData(data), partType: 'psu', psu: { create: specData(data) } } });
  revalidatePath('/psus');
}

export async function updatePsu(id: string, data: PsuFormData) {
  await db.$transaction([
    db.pcPart.update({ where: { id }, data: partData(data) }),
    db.psu.update({ where: { id }, data: specData(data) }),
  ]);
  revalidatePath('/psus');
}

export async function deletePsu(id: string) {
  await db.pcPart.delete({ where: { id } });
  revalidatePath('/psus');
}
