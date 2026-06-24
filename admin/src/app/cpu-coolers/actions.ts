'use server';

import { revalidatePath } from 'next/cache';
import { db } from '@/lib/prisma';
import { splitCommaList, usdToCents } from '@/lib/utils';
import { upsertAmazonAsin } from '@/lib/listings';

export interface CpuCoolerFormData {
  name: string;
  manufacturer: string;
  modelNumber: string;
  yearReleased: number | null;
  isActive: boolean;
  streetPriceUsd: number | null;
  asin: string;
  supportedSocketsInput: string;
  coolerType: string;
  maxTdpWatts: number | null;
  heightMm: number | null;
  radiatorSizeMm: number | null;
  fanCount: number | null;
  fanSizeMm: number | null;
  noiseDba: number | null;
  hasRgb: boolean;
}

const partData = (d: CpuCoolerFormData) => ({
  name: d.name, manufacturer: d.manufacturer || null,
  modelNumber: d.modelNumber || null, yearReleased: d.yearReleased, isActive: d.isActive,
  streetPriceCents: usdToCents(d.streetPriceUsd),
});

const specificData = (d: CpuCoolerFormData) => ({
  supportedSockets: splitCommaList(d.supportedSocketsInput),
  coolerType: d.coolerType || null, maxTdpWatts: d.maxTdpWatts, heightMm: d.heightMm,
  radiatorSizeMm: d.radiatorSizeMm, fanCount: d.fanCount, fanSizeMm: d.fanSizeMm,
  noiseDba: d.noiseDba, hasRgb: d.hasRgb,
});

export async function createCpuCooler(data: CpuCoolerFormData) {
  const created = await db.pcPart.create({ data: { ...partData(data), partType: 'cpucooler', cpuCooler: { create: specificData(data) } } });
  await upsertAmazonAsin(created.id, data.asin);
  revalidatePath('/cpu-coolers');
}

export async function updateCpuCooler(id: string, data: CpuCoolerFormData) {
  await db.$transaction([
    db.pcPart.update({ where: { id }, data: partData(data) }),
    db.cpuCooler.update({ where: { id }, data: specificData(data) }),
  ]);
  await upsertAmazonAsin(id, data.asin);
  revalidatePath('/cpu-coolers');
}

export async function deleteCpuCooler(id: string) {
  await db.pcPart.delete({ where: { id } });
  revalidatePath('/cpu-coolers');
}
