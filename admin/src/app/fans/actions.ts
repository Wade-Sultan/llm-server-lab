'use server';

import { revalidatePath } from 'next/cache';
import { db } from '@/lib/prisma';
import { usdToCents } from '@/lib/utils';

export interface FanFormData {
  name: string;
  manufacturer: string;
  modelNumber: string;
  yearReleased: number | null;
  isActive: boolean;
  streetPriceUsd: number | null;
  sizeMm: number | null;
  maxRpm: number | null;
  airflowCfm: number | null;
  noiseDba: number | null;
  isPwm: boolean;
  hasRgb: boolean;
  bearingType: string;
  isStaticPressure: boolean;
  packCount: number | null;
}

const partData = (d: FanFormData) => ({
  name: d.name, manufacturer: d.manufacturer || null,
  modelNumber: d.modelNumber || null, yearReleased: d.yearReleased, isActive: d.isActive,
  streetPriceCents: usdToCents(d.streetPriceUsd),
});

const specificData = (d: FanFormData) => ({
  sizeMm: d.sizeMm, maxRpm: d.maxRpm, airflowCfm: d.airflowCfm, noiseDba: d.noiseDba,
  isPwm: d.isPwm, hasRgb: d.hasRgb, bearingType: d.bearingType || null,
  isStaticPressure: d.isStaticPressure, packCount: d.packCount,
});

export async function createFan(data: FanFormData) {
  const created = await db.pcPart.create({ data: { ...partData(data), partType: 'fan', fan: { create: specificData(data) } } });
  revalidatePath('/fans');
}

export async function updateFan(id: string, data: FanFormData) {
  await db.$transaction([
    db.pcPart.update({ where: { id }, data: partData(data) }),
    db.fan.update({ where: { id }, data: specificData(data) }),
  ]);
  revalidatePath('/fans');
}

export async function deleteFan(id: string) {
  await db.pcPart.delete({ where: { id } });
  revalidatePath('/fans');
}
