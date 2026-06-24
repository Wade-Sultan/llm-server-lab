'use server';

import { revalidatePath } from 'next/cache';
import { db } from '@/lib/prisma';
import { usdToCents } from '@/lib/utils';
import { upsertAmazonAsin } from '@/lib/listings';

export interface RamFormData {
  name: string;
  manufacturer: string;
  modelNumber: string;
  yearReleased: number | null;
  isActive: boolean;
  streetPriceUsd: number | null;
  asin: string;
  ddrGeneration: string;
  speedMhz: number | null;
  modules: number | null;
  capacityGb: number | null;
  heightMm: number | null;
  moduleCapacityGb: number | null;
  casLatency: number | null;
  voltage: number | null;
  hasRgb: boolean;
  isEcc: boolean;
}

const partData = (d: RamFormData) => ({
  name: d.name, manufacturer: d.manufacturer || null,
  modelNumber: d.modelNumber || null, yearReleased: d.yearReleased, isActive: d.isActive,
  streetPriceCents: usdToCents(d.streetPriceUsd),
});

const specificData = (d: RamFormData) => ({
  ddrGeneration: d.ddrGeneration || null, speedMhz: d.speedMhz, modules: d.modules,
  capacityGb: d.capacityGb, heightMm: d.heightMm, moduleCapacityGb: d.moduleCapacityGb,
  casLatency: d.casLatency, voltage: d.voltage, hasRgb: d.hasRgb, isEcc: d.isEcc,
});

export async function createRam(data: RamFormData) {
  const created = await db.pcPart.create({ data: { ...partData(data), partType: 'ram', ram: { create: specificData(data) } } });
  await upsertAmazonAsin(created.id, data.asin);
  revalidatePath('/ram');
}

export async function updateRam(id: string, data: RamFormData) {
  await db.$transaction([
    db.pcPart.update({ where: { id }, data: partData(data) }),
    db.ram.update({ where: { id }, data: specificData(data) }),
  ]);
  await upsertAmazonAsin(id, data.asin);
  revalidatePath('/ram');
}

export async function deleteRam(id: string) {
  await db.pcPart.delete({ where: { id } });
  revalidatePath('/ram');
}
