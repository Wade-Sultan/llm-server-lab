'use server';

import { revalidatePath } from 'next/cache';
import { db } from '@/lib/prisma';
import { splitCommaList, usdToCents } from '@/lib/utils';

export interface GpuFormData {
  name: string;
  manufacturer: string;
  modelNumber: string;
  yearReleased: number | null;
  isActive: boolean;
  streetPriceUsd: number | null;
  brand: string;
  chipset: string;
  vramGb: number | null;
  tdpWatts: number | null;
  lengthMm: number | null;
  pciePowerPins: string;
  recommendedPsuWatts: number | null;
  vramType: string;
  widthSlots: number | null;
  pcieGeneration: number | null;
  baseClockMhz: number | null;
  boostClockMhz: number | null;
  hasRayTracing: boolean;
  cudaCores: number | null;
  tensorCores: number | null;
  streamProcessors: number | null;
  matrixCores: number | null;
  displayOutputs: string;
  hdmiVersion: string;
  dpVersion: string;
  supportedFeaturesInput: string;
  benchmarkScoresInput: string;
}

export async function createGpu(data: GpuFormData) {
  const created = await db.pcPart.create({
    data: {
      name: data.name,
      manufacturer: data.manufacturer || null,
      modelNumber: data.modelNumber || null,
      yearReleased: data.yearReleased,
      isActive: data.isActive,
      streetPriceCents: usdToCents(data.streetPriceUsd),
      partType: 'gpu',
      gpu: {
        create: {
          brand: data.brand || null,
          chipset: data.chipset || null,
          vramGb: data.vramGb,
          tdpWatts: data.tdpWatts,
          lengthMm: data.lengthMm,
          pciePowerPins: data.pciePowerPins || null,
          recommendedPsuWatts: data.recommendedPsuWatts,
          vramType: data.vramType || null,
          widthSlots: data.widthSlots,
          pcieGeneration: data.pcieGeneration,
          baseClockMhz: data.baseClockMhz,
          boostClockMhz: data.boostClockMhz,
          hasRayTracing: data.hasRayTracing,
          cudaCores: data.cudaCores,
          tensorCores: data.tensorCores,
          streamProcessors: data.streamProcessors,
          matrixCores: data.matrixCores,
          displayOutputs: data.displayOutputs || null,
          hdmiVersion: data.hdmiVersion || null,
          dpVersion: data.dpVersion || null,
          supportedFeatures: splitCommaList(data.supportedFeaturesInput),
          benchmarkScores: data.benchmarkScoresInput
            ? JSON.parse(data.benchmarkScoresInput)
            : undefined,
        },
      },
    },
  });
  revalidatePath('/gpus');
}

export async function updateGpu(id: string, data: GpuFormData) {
  await db.$transaction([
    db.pcPart.update({
      where: { id },
      data: {
        name: data.name,
        manufacturer: data.manufacturer || null,
        modelNumber: data.modelNumber || null,
        yearReleased: data.yearReleased,
        isActive: data.isActive,
        streetPriceCents: usdToCents(data.streetPriceUsd),
      },
    }),
    db.gpu.update({
      where: { id },
      data: {
        brand: data.brand || null,
        chipset: data.chipset || null,
        vramGb: data.vramGb,
        tdpWatts: data.tdpWatts,
        lengthMm: data.lengthMm,
        pciePowerPins: data.pciePowerPins || null,
        recommendedPsuWatts: data.recommendedPsuWatts,
        vramType: data.vramType || null,
        widthSlots: data.widthSlots,
        pcieGeneration: data.pcieGeneration,
        baseClockMhz: data.baseClockMhz,
        boostClockMhz: data.boostClockMhz,
        hasRayTracing: data.hasRayTracing,
        cudaCores: data.cudaCores,
        tensorCores: data.tensorCores,
        streamProcessors: data.streamProcessors,
        matrixCores: data.matrixCores,
        displayOutputs: data.displayOutputs || null,
        hdmiVersion: data.hdmiVersion || null,
        dpVersion: data.dpVersion || null,
        supportedFeatures: splitCommaList(data.supportedFeaturesInput),
        benchmarkScores: data.benchmarkScoresInput
          ? JSON.parse(data.benchmarkScoresInput)
          : null,
      },
    }),
  ]);
  revalidatePath('/gpus');
}

export async function deleteGpu(id: string) {
  await db.pcPart.delete({ where: { id } });
  revalidatePath('/gpus');
}
