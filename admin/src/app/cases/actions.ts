'use server';

import { revalidatePath } from 'next/cache';
import { db } from '@/lib/prisma';
import { splitCommaList, usdToCents } from '@/lib/utils';
import { upsertAmazonAsin } from '@/lib/listings';

export interface CaseFormData {
  name: string;
  manufacturer: string;
  modelNumber: string;
  yearReleased: number | null;
  isActive: boolean;
  streetPriceUsd: number | null;
  asin: string;
  supportedMoboFormFactorsInput: string;
  maxGpuLengthMm: number | null;
  maxCoolerHeightMm: number | null;
  maxRadiatorFrontMm: number | null;
  maxRadiatorTopMm: number | null;
  maxPsuLengthMm: number | null;
  includedFanCount: number | null;
  chamberCount: number | null;
  frontPanelMesh: boolean;
  color: string;
  size: string;
  driveBays35: number | null;
  driveBays25: number | null;
  maxFanSlots: number | null;
  hasGlassPanel: boolean;
  weightKg: number | null;
  lengthMm: number | null;
  widthMm: number | null;
  heightMm: number | null;
  usbFrontTypeA: number | null;
  usbFrontTypeC: number | null;
}

const partData = (d: CaseFormData) => ({
  name: d.name, manufacturer: d.manufacturer || null,
  modelNumber: d.modelNumber || null, yearReleased: d.yearReleased, isActive: d.isActive,
  streetPriceCents: usdToCents(d.streetPriceUsd),
});

const specificData = (d: CaseFormData) => ({
  supportedMoboFormFactors: splitCommaList(d.supportedMoboFormFactorsInput),
  maxGpuLengthMm: d.maxGpuLengthMm, maxCoolerHeightMm: d.maxCoolerHeightMm,
  maxRadiatorFrontMm: d.maxRadiatorFrontMm, maxRadiatorTopMm: d.maxRadiatorTopMm,
  maxPsuLengthMm: d.maxPsuLengthMm, includedFanCount: d.includedFanCount,
  chamberCount: d.chamberCount, frontPanelMesh: d.frontPanelMesh,
  color: d.color || null, size: d.size || null, driveBays35: d.driveBays35,
  driveBays25: d.driveBays25, maxFanSlots: d.maxFanSlots, hasGlassPanel: d.hasGlassPanel,
  weightKg: d.weightKg, lengthMm: d.lengthMm, widthMm: d.widthMm, heightMm: d.heightMm,
  usbFrontTypeA: d.usbFrontTypeA, usbFrontTypeC: d.usbFrontTypeC,
});

export async function createCase(data: CaseFormData) {
  const created = await db.pcPart.create({ data: { ...partData(data), partType: 'case', pcCase: { create: specificData(data) } } });
  await upsertAmazonAsin(created.id, data.asin);
  revalidatePath('/cases');
}

export async function updateCase(id: string, data: CaseFormData) {
  await db.$transaction([
    db.pcPart.update({ where: { id }, data: partData(data) }),
    db.pcCase.update({ where: { id }, data: specificData(data) }),
  ]);
  await upsertAmazonAsin(id, data.asin);
  revalidatePath('/cases');
}

export async function deleteCase(id: string) {
  await db.pcPart.delete({ where: { id } });
  revalidatePath('/cases');
}
