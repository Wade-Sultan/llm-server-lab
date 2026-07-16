'use server';

import { revalidatePath } from 'next/cache';
import { db } from '@/lib/prisma';
import { splitCommaList, slugify } from '@/lib/utils';

export interface AiWorkloadFormData {
  id: string | null; // null = new row
  task: string;
  precision: string;
  gpuImportance: string;
  minVramGb: number | null;
  recommendedVramGb: number | null;
  supportsMultiGpu: boolean;
  cpuOffloadCapable: boolean;
  minRamGb: number | null;
  recommendedRamGb: number | null;
  minStorageGb: number | null;
  gpuBackendsInput: string;
  requiredGpuFeaturesInput: string;
  assumptionsInput: string; // JSON
  notes: string;
}

export interface AiModelFormData {
  name: string;
  slug: string;
  family: string;
  paramsBillions: number | null;
  contextLength: number | null;
  developer: string;
  license: string;
  huggingfaceId: string;
  websiteUrl: string;
  imageUrl: string;
  specInput: string; // JSON
  notes: string;
  workloads: AiWorkloadFormData[];
}

function toData(d: AiModelFormData) {
  return {
    name: d.name,
    slug: d.slug ? slugify(d.slug) : slugify(d.name),
    family: d.family,
    paramsBillions: d.paramsBillions,
    contextLength: d.contextLength,
    developer: d.developer || null,
    license: d.license || null,
    huggingfaceId: d.huggingfaceId || null,
    websiteUrl: d.websiteUrl || null,
    imageUrl: d.imageUrl || null,
    spec: d.specInput ? JSON.parse(d.specInput) : null,
    notes: d.notes || null,
  };
}

function toWorkloadData(w: AiWorkloadFormData, sortOrder: number) {
  return {
    task: w.task,
    precision: w.precision,
    sortOrder,
    gpuImportance: w.gpuImportance,
    minVramGb: w.minVramGb,
    recommendedVramGb: w.recommendedVramGb,
    supportsMultiGpu: w.supportsMultiGpu,
    cpuOffloadCapable: w.cpuOffloadCapable,
    minRamGb: w.minRamGb,
    recommendedRamGb: w.recommendedRamGb,
    minStorageGb: w.minStorageGb,
    gpuBackends: splitCommaList(w.gpuBackendsInput),
    requiredGpuFeatures: splitCommaList(w.requiredGpuFeaturesInput),
    assumptions: w.assumptionsInput ? JSON.parse(w.assumptionsInput) : null,
    notes: w.notes || null,
  };
}

export async function createAiModel(data: AiModelFormData) {
  await db.aiModel.create({
    data: {
      ...toData(data),
      workloads: { create: data.workloads.map((w, i) => toWorkloadData(w, i)) },
    },
  });
  revalidatePath('/ai-models');
}

// Workloads are diffed by id (update existing, create new, delete removed)
// rather than replaced wholesale, so columns the form doesn't carry
// (extra_requirements) survive edits.
export async function updateAiModel(id: string, data: AiModelFormData) {
  const keptIds = data.workloads.map((w) => w.id).filter((x): x is string => !!x);

  await db.$transaction(async (tx) => {
    await tx.aiModel.update({ where: { id }, data: toData(data) });
    await tx.aiWorkload.deleteMany({ where: { modelId: id, id: { notIn: keptIds } } });
    for (const [i, workload] of data.workloads.entries()) {
      if (workload.id) {
        await tx.aiWorkload.update({ where: { id: workload.id }, data: toWorkloadData(workload, i) });
      } else {
        await tx.aiWorkload.create({ data: { modelId: id, ...toWorkloadData(workload, i) } });
      }
    }
  });
  revalidatePath('/ai-models');
}

export async function deleteAiModel(id: string) {
  await db.aiModel.delete({ where: { id } });
  revalidatePath('/ai-models');
}
