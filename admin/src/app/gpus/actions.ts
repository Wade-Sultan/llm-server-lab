'use server';

import { revalidatePath } from 'next/cache';
import { db } from '@/lib/prisma';
import { usdToCents } from '@/lib/utils';

export interface GpuFormData {
  name: string;
  manufacturer: string;
  modelNumber: string;
  yearReleased: number | null;
  isActive: boolean;
  streetPriceUsd: number | null;
  gpuChipsetId: string;
  brand: string;
  lengthMm: number | null;
  widthSlots: number | null;
  pciePowerPins: string;
  displayOutputs: string;
  hdmiVersion: string;
  dpVersion: string;
}

function partData(d: GpuFormData) {
  return {
    name: d.name,
    manufacturer: d.manufacturer || null,
    modelNumber: d.modelNumber || null,
    yearReleased: d.yearReleased,
    isActive: d.isActive,
    streetPriceCents: usdToCents(d.streetPriceUsd),
  };
}

function specData(d: GpuFormData) {
  return {
    gpuChipsetId: d.gpuChipsetId,
    brand: d.brand,
    lengthMm: d.lengthMm ?? 0,
    widthSlots: d.widthSlots,
    pciePowerPins: d.pciePowerPins || null,
    displayOutputs: d.displayOutputs || null,
    hdmiVersion: d.hdmiVersion || null,
    dpVersion: d.dpVersion || null,
  };
}

export async function createGpu(data: GpuFormData) {
  await db.pcPart.create({
    data: { ...partData(data), partType: 'gpu', gpu: { create: specData(data) } },
  });
  revalidatePath('/gpus');
}

export async function updateGpu(id: string, data: GpuFormData) {
  await db.$transaction([
    db.pcPart.update({ where: { id }, data: partData(data) }),
    db.gpu.update({ where: { id }, data: specData(data) }),
  ]);
  revalidatePath('/gpus');
}

export async function deleteGpu(id: string) {
  await db.pcPart.delete({ where: { id } });
  revalidatePath('/gpus');
}
