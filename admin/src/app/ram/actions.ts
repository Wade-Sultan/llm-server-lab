'use server';

import { revalidatePath } from 'next/cache';
import { db } from '@/lib/prisma';

export interface RamFormData {
  name: string;
  manufacturer: string;
  modelNumber: string;
  yearReleased: number | null;
  isActive: boolean;
  ramGroupId: string;
  heightMm: number | null;
  hasRgb: boolean;
}

const partData = (d: RamFormData) => ({
  name: d.name, manufacturer: d.manufacturer || null,
  modelNumber: d.modelNumber || null, yearReleased: d.yearReleased, isActive: d.isActive,
});

const specData = (d: RamFormData) => ({
  ramGroupId: d.ramGroupId, heightMm: d.heightMm, hasRgb: d.hasRgb,
});

export async function createRam(data: RamFormData) {
  await db.pcPart.create({ data: { ...partData(data), partType: 'ramkit', ramKit: { create: specData(data) } } });
  revalidatePath('/ram');
}

export async function updateRam(id: string, data: RamFormData) {
  await db.$transaction([
    db.pcPart.update({ where: { id }, data: partData(data) }),
    db.ramKit.update({ where: { id }, data: specData(data) }),
  ]);
  revalidatePath('/ram');
}

export async function deleteRam(id: string) {
  await db.pcPart.delete({ where: { id } });
  revalidatePath('/ram');
}
