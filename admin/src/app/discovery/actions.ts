'use server';

import { revalidatePath } from 'next/cache';
import type { Prisma } from '@prisma/client';
import { db } from '@/lib/prisma';
import { splitCommaList, usdToCents } from '@/lib/utils';

// Unlike the other pages' actions, everything here returns { error?: string }
// instead of throwing — thrown server-action messages are masked in
// production, and approval failures (duplicate name, already reviewed) need
// to reach the reviewer verbatim.

export type DiscoveryCategory = 'cpu' | 'gpu_chipset' | 'gpu_variant';

async function postDiscovery(
  path: string,
  body: Record<string, unknown>,
): Promise<{ runId?: string; error?: string }> {
  const base = process.env.BACKEND_API_URL;
  const key = process.env.DISCOVERY_API_KEY;
  if (!base || !key) {
    // In the cluster these come from the admin-config ConfigMap and the
    // palladium-secrets Secret, not this file — check the pod's env first.
    return { error: 'BACKEND_API_URL and DISCOVERY_API_KEY must be set in admin/.env.local' };
  }

  let res: Response;
  try {
    res = await fetch(`${base}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Admin-Key': key },
      body: JSON.stringify(body),
      cache: 'no-store',
    });
  } catch {
    return { error: `Cannot reach backend at ${base} — is it running?` };
  }

  if (res.status === 202) {
    const body = (await res.json()) as { run_id: string };
    revalidatePath('/discovery');
    return { runId: body.run_id };
  }

  let detail = '';
  try {
    detail = ((await res.json()) as { detail?: string }).detail ?? '';
  } catch {
    // non-JSON error body
  }
  if (res.status === 503) {
    return { error: `Backend not configured for discovery: ${detail || 'missing API keys'}` };
  }
  if (res.status === 403) {
    return { error: 'Backend rejected DISCOVERY_API_KEY — make sure admin and backend use the same value' };
  }
  return { error: `Discovery trigger failed (${res.status}): ${detail || res.statusText}` };
}

/** Discover one named part. */
export async function triggerDiscovery(
  query: string,
  category: DiscoveryCategory,
): Promise<{ runId?: string; error?: string }> {
  return postDiscovery('/api/v1/discovery/runs', { query, category });
}

/** Enumerate what's new in a category and discover each candidate. One run
 * row, up to DISCOVERY_SWEEP_MAX_CANDIDATES items — costs that many times a
 * single-part run, so the UI warns before firing it. */
export async function triggerSweep(
  hint: string,
  category: DiscoveryCategory,
): Promise<{ runId?: string; error?: string }> {
  return postDiscovery('/api/v1/discovery/sweeps', {
    category,
    hint: hint.trim() || null,
  });
}

export interface ApproveCpuFormData {
  name: string;
  manufacturer: string;
  modelNumber: string;
  yearReleased: number | null;
  msrpUsd: number | null;
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
}

export interface ApproveGpuChipsetFormData {
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
}

export interface ApproveGpuVariantFormData {
  name: string;
  manufacturer: string;
  modelNumber: string;
  yearReleased: number | null;
  msrpUsd: number | null;
  gpuChipsetId: string;
  brand: string;
  lengthMm: number | null;
  widthSlots: number | null;
  pciePowerPins: string;
  displayOutputs: string;
  hdmiVersion: string;
  dpVersion: string;
}

/** Flip the pending item to approved inside the same transaction that created
 * the catalog row; count 0 means someone else reviewed it first — throw so the
 * created row rolls back. */
async function markApproved(
  tx: Prisma.TransactionClient,
  itemId: string,
  link: { createdPartId?: string; createdChipsetId?: string },
) {
  const { count } = await tx.discoveredItem.updateMany({
    where: { id: itemId, reviewStatus: 'pending' },
    data: { reviewStatus: 'approved', reviewedAt: new Date(), ...link },
  });
  if (count === 0) throw new Error('Item was already reviewed');
}

export async function approveCpu(
  itemId: string,
  data: ApproveCpuFormData,
): Promise<{ error?: string }> {
  try {
    await db.$transaction(async (tx) => {
      const existing = await tx.pcPart.findFirst({
        where: { partType: 'cpu', name: { equals: data.name, mode: 'insensitive' } },
        select: { id: true },
      });
      if (existing) {
        throw new Error(`A CPU named "${data.name}" already exists — use Mark duplicate instead`);
      }
      const part = await tx.pcPart.create({
        data: {
          name: data.name,
          manufacturer: data.manufacturer || null,
          modelNumber: data.modelNumber || null,
          yearReleased: data.yearReleased,
          msrpCents: usdToCents(data.msrpUsd),
          isActive: true,
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
            },
          },
        },
      });
      await markApproved(tx, itemId, { createdPartId: part.id });
    });
  } catch (e) {
    return { error: e instanceof Error ? e.message : 'Approval failed' };
  }
  revalidatePath('/discovery');
  revalidatePath('/cpus');
  return {};
}

export async function approveGpuChipset(
  itemId: string,
  data: ApproveGpuChipsetFormData,
): Promise<{ error?: string }> {
  try {
    await db.$transaction(async (tx) => {
      const existing = await tx.gpuChipset.findFirst({
        where: { name: { equals: data.name, mode: 'insensitive' } },
        select: { id: true },
      });
      if (existing) {
        throw new Error(`A chipset named "${data.name}" already exists — use Mark duplicate instead`);
      }
      const chipset = await tx.gpuChipset.create({
        data: {
          name: data.name,
          vramGb: data.vramGb ?? 0,
          vramType: data.vramType || null,
          tdpWatts: data.tdpWatts ?? 0,
          recommendedPsuWatts: data.recommendedPsuWatts,
          pcieGeneration: data.pcieGeneration,
          baseClockMhz: data.baseClockMhz,
          boostClockMhz: data.boostClockMhz,
          hasRayTracing: data.hasRayTracing,
          cudaCores: data.cudaCores,
          tensorCores: data.tensorCores,
          streamProcessors: data.streamProcessors,
          matrixCores: data.matrixCores,
          supportedFeatures: splitCommaList(data.supportedFeaturesInput),
        },
      });
      await markApproved(tx, itemId, { createdChipsetId: chipset.id });
    });
  } catch (e) {
    return { error: e instanceof Error ? e.message : 'Approval failed' };
  }
  revalidatePath('/discovery');
  revalidatePath('/gpu-chipsets');
  return {};
}

export async function approveGpuVariant(
  itemId: string,
  data: ApproveGpuVariantFormData,
): Promise<{ error?: string }> {
  try {
    await db.$transaction(async (tx) => {
      const existing = await tx.pcPart.findFirst({
        where: { partType: 'gpu', name: { equals: data.name, mode: 'insensitive' } },
        select: { id: true },
      });
      if (existing) {
        throw new Error(`A GPU named "${data.name}" already exists — use Mark duplicate instead`);
      }
      const part = await tx.pcPart.create({
        data: {
          name: data.name,
          manufacturer: data.manufacturer || null,
          modelNumber: data.modelNumber || null,
          yearReleased: data.yearReleased,
          msrpCents: usdToCents(data.msrpUsd),
          isActive: true,
          partType: 'gpu',
          gpu: {
            create: {
              gpuChipsetId: data.gpuChipsetId,
              brand: data.brand,
              lengthMm: data.lengthMm ?? 0,
              widthSlots: data.widthSlots,
              pciePowerPins: data.pciePowerPins || null,
              displayOutputs: data.displayOutputs || null,
              hdmiVersion: data.hdmiVersion || null,
              dpVersion: data.dpVersion || null,
            },
          },
        },
      });
      await markApproved(tx, itemId, { createdPartId: part.id });
    });
  } catch (e) {
    return { error: e instanceof Error ? e.message : 'Approval failed' };
  }
  revalidatePath('/discovery');
  revalidatePath('/gpus');
  return {};
}

export async function rejectItem(itemId: string): Promise<{ error?: string }> {
  await db.discoveredItem.updateMany({
    where: { id: itemId, reviewStatus: 'pending' },
    data: { reviewStatus: 'rejected', reviewedAt: new Date() },
  });
  revalidatePath('/discovery');
  return {};
}

export async function markDuplicate(itemId: string): Promise<{ error?: string }> {
  await db.discoveredItem.updateMany({
    where: { id: itemId, reviewStatus: 'pending' },
    data: { reviewStatus: 'duplicate', reviewedAt: new Date() },
  });
  revalidatePath('/discovery');
  return {};
}
