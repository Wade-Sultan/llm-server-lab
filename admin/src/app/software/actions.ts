'use server';

import { revalidatePath } from 'next/cache';
import { db } from '@/lib/prisma';
import { splitCommaList, slugify } from '@/lib/utils';

export interface SoftwareTierFormData {
  id: string | null; // null = new row
  name: string;
  slug: string;
  gpuImportance: string;
  minRamGb: number | null;
  recommendedRamGb: number | null;
  minVramGb: number | null;
  minStorageGb: number | null;
  minCores: number | null;
  prefersSingleThread: boolean;
  notes: string;
}

export interface SoftwareFormData {
  name: string;
  slug: string;
  category: string;
  useCaseTagsInput: string;
  developer: string;
  currentVersion: string;
  websiteUrl: string;
  imageUrl: string;
  isFree: boolean;
  platformRequirementsInput: string;
  notes: string;
  tiers: SoftwareTierFormData[];
}

function toData(d: SoftwareFormData) {
  return {
    name: d.name,
    slug: d.slug ? slugify(d.slug) : slugify(d.name),
    category: d.category,
    useCaseTags: splitCommaList(d.useCaseTagsInput),
    developer: d.developer || null,
    currentVersion: d.currentVersion || null,
    websiteUrl: d.websiteUrl || null,
    imageUrl: d.imageUrl || null,
    isFree: d.isFree,
    platformRequirements: splitCommaList(d.platformRequirementsInput),
    notes: d.notes || null,
  };
}

function toTierData(t: SoftwareTierFormData, sortOrder: number) {
  return {
    name: t.name,
    slug: t.slug ? slugify(t.slug) : slugify(t.name),
    sortOrder,
    gpuImportance: t.gpuImportance,
    minRamGb: t.minRamGb,
    recommendedRamGb: t.recommendedRamGb,
    minVramGb: t.minVramGb,
    minStorageGb: t.minStorageGb,
    minCores: t.minCores,
    prefersSingleThread: t.prefersSingleThread,
    notes: t.notes || null,
  };
}

export async function createSoftware(data: SoftwareFormData) {
  await db.software.create({
    data: {
      ...toData(data),
      tiers: { create: data.tiers.map((t, i) => toTierData(t, i)) },
    },
  });
  revalidatePath('/software');
}

// Tiers are diffed by id (update existing, create new, delete removed) rather
// than replaced wholesale: a tier row owns extra_requirements JSON and
// software_minimum_parts children that the form doesn't carry, and a
// delete-and-recreate would silently destroy them.
export async function updateSoftware(id: string, data: SoftwareFormData) {
  const keptIds = data.tiers.map((t) => t.id).filter((x): x is string => !!x);

  await db.$transaction(async (tx) => {
    await tx.software.update({ where: { id }, data: toData(data) });
    await tx.softwareTier.deleteMany({ where: { softwareId: id, id: { notIn: keptIds } } });
    for (const [i, tier] of data.tiers.entries()) {
      if (tier.id) {
        await tx.softwareTier.update({ where: { id: tier.id }, data: toTierData(tier, i) });
      } else {
        await tx.softwareTier.create({ data: { softwareId: id, ...toTierData(tier, i) } });
      }
    }
  });
  revalidatePath('/software');
}

export async function deleteSoftware(id: string) {
  await db.software.delete({ where: { id } });
  revalidatePath('/software');
}
