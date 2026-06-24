'use server';

import { revalidatePath } from 'next/cache';
import { db } from '@/lib/prisma';
import { usdToCents } from '@/lib/utils';
import { upsertAmazonAsin } from '@/lib/listings';

export interface PsuFormData {
  name: string;
  manufacturer: string;
  modelNumber: string;
  yearReleased: number | null;
  isActive: boolean;
  streetPriceUsd: number | null;
  asin: string;
  wattage: number | null;
  formFactor: string;
  efficiencyRating: string;
  pcie8pinConnectors: number | null;
  pcie12pinConnectors: number | null;
  pcie16pinConnectors: number | null;
  depthMm: number | null;
  modular: string;
  epsConnectors: number | null;
  fanSizeMm: number | null;
  isFanless: boolean;
}

const partData = (d: PsuFormData) => ({
  name: d.name, manufacturer: d.manufacturer || null,
  modelNumber: d.modelNumber || null, yearReleased: d.yearReleased, isActive: d.isActive,
  streetPriceCents: usdToCents(d.streetPriceUsd),
});

const specificData = (d: PsuFormData) => ({
  wattage: d.wattage, formFactor: d.formFactor || null,
  efficiencyRating: d.efficiencyRating || null, pcie8pinConnectors: d.pcie8pinConnectors,
  pcie12pinConnectors: d.pcie12pinConnectors, pcie16pinConnectors: d.pcie16pinConnectors,
  depthMm: d.depthMm, modular: d.modular || null, epsConnectors: d.epsConnectors,
  fanSizeMm: d.fanSizeMm, isFanless: d.isFanless,
});

export async function createPsu(data: PsuFormData) {
  const created = await db.pcPart.create({ data: { ...partData(data), partType: 'psu', psu: { create: specificData(data) } } });
  await upsertAmazonAsin(created.id, data.asin);
  revalidatePath('/psus');
}

export async function updatePsu(id: string, data: PsuFormData) {
  await db.$transaction([
    db.pcPart.update({ where: { id }, data: partData(data) }),
    db.psu.update({ where: { id }, data: specificData(data) }),
  ]);
  await upsertAmazonAsin(id, data.asin);
  revalidatePath('/psus');
}

export async function deletePsu(id: string) {
  await db.pcPart.delete({ where: { id } });
  revalidatePath('/psus');
}
