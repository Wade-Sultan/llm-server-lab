'use server';

import { revalidatePath } from 'next/cache';
import { db } from '@/lib/prisma';
import { splitCommaList } from '@/lib/utils';

export interface GpuChipsetFormData {
  name: string;
  vramGb: number | null;
  vramType: string;
  tdpWatts: number | null;
  recommendedPsuWatts: number | null;
  pcieGeneration: number | null;
  baseClockMhz: number | null;
  boostClockMhz: number | null;
  hasRayTracing: boolean;
  cudaCores: number | null;
  tensorCores: number | null;
  streamProcessors: number | null;
  matrixCores: number | null;
  supportedFeaturesInput: string;
  benchmarkScoresInput: string;
}

function toData(d: GpuChipsetFormData) {
  return {
    name: d.name,
    vramGb: d.vramGb ?? 0,
    vramType: d.vramType || null,
    tdpWatts: d.tdpWatts ?? 0,
    recommendedPsuWatts: d.recommendedPsuWatts,
    pcieGeneration: d.pcieGeneration,
    baseClockMhz: d.baseClockMhz,
    boostClockMhz: d.boostClockMhz,
    hasRayTracing: d.hasRayTracing,
    cudaCores: d.cudaCores,
    tensorCores: d.tensorCores,
    streamProcessors: d.streamProcessors,
    matrixCores: d.matrixCores,
    supportedFeatures: splitCommaList(d.supportedFeaturesInput),
    benchmarkScores: d.benchmarkScoresInput ? JSON.parse(d.benchmarkScoresInput) : undefined,
  };
}

export async function createGpuChipset(data: GpuChipsetFormData) {
  await db.gpuChipset.create({ data: toData(data) });
  revalidatePath('/gpu-chipsets');
}

export async function updateGpuChipset(id: string, data: GpuChipsetFormData) {
  const { benchmarkScores, ...rest } = toData(data);
  await db.gpuChipset.update({
    where: { id },
    data: { ...rest, benchmarkScores: benchmarkScores ?? null },
  });
  revalidatePath('/gpu-chipsets');
}

export async function deleteGpuChipset(id: string) {
  await db.gpuChipset.delete({ where: { id } });
  revalidatePath('/gpu-chipsets');
}
