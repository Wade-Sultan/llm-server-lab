'use server';

import { revalidatePath } from 'next/cache';
import { db } from '@/lib/prisma';

export interface StorageFormData {
  name: string;
  manufacturer: string;
  modelNumber: string;
  yearReleased: number | null;
  isActive: boolean;
  storageType: string;
  formFactor: string;
  interface: string;
  capacityGb: number | null;
  readSpeedMbps: number | null;
  writeSpeedMbps: number | null;
  hasDramCache: boolean;
  enduranceTbw: number | null;
  rpm: number | null;
}

const partData = (d: StorageFormData) => ({
  name: d.name, manufacturer: d.manufacturer || null,
  modelNumber: d.modelNumber || null, yearReleased: d.yearReleased, isActive: d.isActive,
});

const specificData = (d: StorageFormData) => ({
  storageType: d.storageType || null, formFactor: d.formFactor || null,
  interface: d.interface || null, capacityGb: d.capacityGb, readSpeedMbps: d.readSpeedMbps,
  writeSpeedMbps: d.writeSpeedMbps, hasDramCache: d.hasDramCache,
  enduranceTbw: d.enduranceTbw, rpm: d.rpm,
});

export async function createStorage(data: StorageFormData) {
  await db.pcPart.create({ data: { ...partData(data), partType: 'storage', storage: { create: specificData(data) } } });
  revalidatePath('/storage');
}

export async function updateStorage(id: string, data: StorageFormData) {
  await db.$transaction([
    db.pcPart.update({ where: { id }, data: partData(data) }),
    db.storage.update({ where: { id }, data: specificData(data) }),
  ]);
  revalidatePath('/storage');
}

export async function deleteStorage(id: string) {
  await db.pcPart.delete({ where: { id } });
  revalidatePath('/storage');
}
