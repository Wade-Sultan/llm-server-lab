'use server';

import { revalidatePath } from 'next/cache';
import { db } from '@/lib/prisma';
import { usdToCents } from '@/lib/utils';

export interface StorageGroupFormData {
  name: string;
  streetPriceUsd: number | null;
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

function toData(d: StorageGroupFormData) {
  return {
    name: d.name,
    streetPriceCents: usdToCents(d.streetPriceUsd),
    storageType: d.storageType,
    formFactor: d.formFactor,
    interface: d.interface,
    capacityGb: d.capacityGb ?? 0,
    readSpeedMbps: d.readSpeedMbps,
    writeSpeedMbps: d.writeSpeedMbps,
    hasDramCache: d.hasDramCache,
    enduranceTbw: d.enduranceTbw,
    rpm: d.rpm,
  };
}

export async function createStorageGroup(data: StorageGroupFormData) {
  await db.storageGroup.create({ data: toData(data) });
  revalidatePath('/storage-groups');
}

export async function updateStorageGroup(id: string, data: StorageGroupFormData) {
  await db.storageGroup.update({ where: { id }, data: toData(data) });
  revalidatePath('/storage-groups');
}

export async function deleteStorageGroup(id: string) {
  await db.storageGroup.delete({ where: { id } });
  revalidatePath('/storage-groups');
}
