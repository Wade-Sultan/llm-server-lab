'use server';

import { revalidatePath } from 'next/cache';
import { db } from '@/lib/prisma';

export interface StorageFormData {
  name: string;
  manufacturer: string;
  modelNumber: string;
  yearReleased: number | null;
  isActive: boolean;
  storageGroupId: string;
}

const partData = (d: StorageFormData) => ({
  name: d.name, manufacturer: d.manufacturer || null,
  modelNumber: d.modelNumber || null, yearReleased: d.yearReleased, isActive: d.isActive,
});

export async function createStorage(data: StorageFormData) {
  await db.pcPart.create({
    data: { ...partData(data), partType: 'storagedrive', storageDrive: { create: { storageGroupId: data.storageGroupId } } },
  });
  revalidatePath('/storage');
}

export async function updateStorage(id: string, data: StorageFormData) {
  await db.$transaction([
    db.pcPart.update({ where: { id }, data: partData(data) }),
    db.storageDrive.update({ where: { id }, data: { storageGroupId: data.storageGroupId } }),
  ]);
  revalidatePath('/storage');
}

export async function deleteStorage(id: string) {
  await db.pcPart.delete({ where: { id } });
  revalidatePath('/storage');
}
