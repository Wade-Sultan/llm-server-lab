'use server';

import { revalidatePath } from 'next/cache';
import { db } from '@/lib/prisma';
import { splitCommaList } from '@/lib/utils';

export interface CpuFormData {
  name: string;
  manufacturer: string;
  modelNumber: string;
  yearReleased: number | null;
  isActive: boolean;
  brand: string;
  socket: string;
  tdpWatts: number | null;
  hasIgpu: boolean;
  ddrGenerationInput: string;
  cores: number | null;
  threads: number | null;
  baseClockGhz: number | null;
  boostClockGhz: number | null;
  l3CacheMb: number | null;
  pcieGeneration: number | null;
  maxMemoryGb: number | null;
  series: string;
  supportedFeaturesInput: string;
  benchmarkScoresInput: string;
}

export async function createCpu(data: CpuFormData) {
  await db.pcPart.create({
    data: {
      name: data.name,
      manufacturer: data.manufacturer || null,
      modelNumber: data.modelNumber || null,
      yearReleased: data.yearReleased,
      isActive: data.isActive,
      partType: 'cpu',
      cpu: {
        create: {
          brand: data.brand || null,
          socket: data.socket || null,
          tdpWatts: data.tdpWatts,
          hasIgpu: data.hasIgpu,
          ddrGeneration: splitCommaList(data.ddrGenerationInput),
          cores: data.cores,
          threads: data.threads,
          baseClockGhz: data.baseClockGhz,
          boostClockGhz: data.boostClockGhz,
          l3CacheMb: data.l3CacheMb,
          pcieGeneration: data.pcieGeneration,
          maxMemoryGb: data.maxMemoryGb,
          series: data.series || null,
          supportedFeatures: splitCommaList(data.supportedFeaturesInput),
          benchmarkScores: data.benchmarkScoresInput
            ? JSON.parse(data.benchmarkScoresInput)
            : undefined,
        },
      },
    },
  });
  revalidatePath('/cpus');
}

export async function updateCpu(id: string, data: CpuFormData) {
  await db.$transaction([
    db.pcPart.update({
      where: { id },
      data: {
        name: data.name,
        manufacturer: data.manufacturer || null,
        modelNumber: data.modelNumber || null,
        yearReleased: data.yearReleased,
        isActive: data.isActive,
      },
    }),
    db.cpu.update({
      where: { id },
      data: {
        brand: data.brand || null,
        socket: data.socket || null,
        tdpWatts: data.tdpWatts,
        hasIgpu: data.hasIgpu,
        ddrGeneration: splitCommaList(data.ddrGenerationInput),
        cores: data.cores,
        threads: data.threads,
        baseClockGhz: data.baseClockGhz,
        boostClockGhz: data.boostClockGhz,
        l3CacheMb: data.l3CacheMb,
        pcieGeneration: data.pcieGeneration,
        maxMemoryGb: data.maxMemoryGb,
        series: data.series || null,
        supportedFeatures: splitCommaList(data.supportedFeaturesInput),
        benchmarkScores: data.benchmarkScoresInput
          ? JSON.parse(data.benchmarkScoresInput)
          : null,
      },
    }),
  ]);
  revalidatePath('/cpus');
}

export async function deleteCpu(id: string) {
  await db.pcPart.delete({ where: { id } });
  revalidatePath('/cpus');
}
