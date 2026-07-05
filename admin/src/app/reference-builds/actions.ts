'use server';

import { revalidatePath } from 'next/cache';
import { db } from '@/lib/prisma';
import type { BuildComponentRole } from '@prisma/client';

export interface ReferenceBuildFormData {
  buildKey: string;
  label: string;
  description: string;
  isActive: boolean;
  maxResolution: number | null;
  cpuId: string | null;
  motherboardId: string | null;
  ramId: string | null;
  psuId: string | null;
  caseId: string | null;
  cpuCoolerId: string | null;
  gpuIds: string[];
  storageIds: string[];
  fanIds: string[];
}

type PartEntry = { partId: string; component: BuildComponentRole };

const REQUIRED: Record<BuildComponentRole, boolean> = {
  cpu: true, cpucooler: true, motherboard: true,
  ram: true, storage: true, psu: true, case: true,
  gpu: false, fan: false,
};

function collectEntries(data: ReferenceBuildFormData): PartEntry[] {
  const entries: PartEntry[] = [];
  if (data.cpuId)        entries.push({ partId: data.cpuId,        component: 'cpu' });
  if (data.motherboardId) entries.push({ partId: data.motherboardId, component: 'motherboard' });
  if (data.ramId)        entries.push({ partId: data.ramId,        component: 'ram' });
  if (data.psuId)        entries.push({ partId: data.psuId,        component: 'psu' });
  if (data.caseId)       entries.push({ partId: data.caseId,       component: 'case' });
  if (data.cpuCoolerId)  entries.push({ partId: data.cpuCoolerId,  component: 'cpucooler' });
  for (const id of data.gpuIds)     entries.push({ partId: id, component: 'gpu' });
  for (const id of data.storageIds) entries.push({ partId: id, component: 'storage' });
  for (const id of data.fanIds)     entries.push({ partId: id, component: 'fan' });
  return entries;
}

async function fetchPrices(partIds: string[]): Promise<Map<string, number>> {
  if (!partIds.length) return new Map();
  const parts = await db.pcPart.findMany({
    where: { id: { in: partIds } },
    select: { id: true, streetPriceCents: true },
  });
  return new Map(parts.map(p => [p.id, p.streetPriceCents ?? 0]));
}

// pc_build_parts requires one-per-role; take the first occurrence of each component.
function buildPcBuildParts(entries: PartEntry[], prices: Map<string, number>) {
  const seen = new Set<string>();
  return entries
    .filter(e => { if (seen.has(e.component)) return false; seen.add(e.component); return true; })
    .map(e => ({
      partId: e.partId,
      role: e.component,
      requiredComponent: REQUIRED[e.component] ?? false,
      priceAtBuild: prices.get(e.partId) ?? null,
    }));
}

export async function createReferenceBuild(data: ReferenceBuildFormData) {
  const entries = collectEntries(data);
  const prices = await fetchPrices(entries.map(e => e.partId));
  const totalApprox = [...prices.values()].reduce((s, p) => s + p, 0) || null;

  const pcBuild = await db.pCBuild.create({
    data: {
      name: data.label,
      description: data.description || null,
      status: 'finalized',
      totalPriceCents: totalApprox,
      parts: { create: buildPcBuildParts(entries, prices) },
    },
  });

  await db.referenceBuild.create({
    data: {
      buildKey: data.buildKey,
      label: data.label,
      description: data.description || null,
      isActive: data.isActive,
      maxResolution: data.maxResolution,
      totalApprox,
      pcBuildId: pcBuild.id,
      parts: {
        create: entries.map((e, i) => ({
          partId: e.partId,
          component: e.component,
          sortOrder: i,
          approxPrice: prices.get(e.partId) ?? 0,
        })),
      },
    },
  });

  revalidatePath('/reference-builds');
}

export async function updateReferenceBuild(id: string, data: ReferenceBuildFormData) {
  const entries = collectEntries(data);
  const prices = await fetchPrices(entries.map(e => e.partId));
  const totalApprox = [...prices.values()].reduce((s, p) => s + p, 0) || null;

  const existing = await db.referenceBuild.findUniqueOrThrow({
    where: { id },
    select: { pcBuildId: true },
  });

  await db.$transaction(async tx => {
    if (existing.pcBuildId) {
      await tx.pCBuild.update({
        where: { id: existing.pcBuildId },
        data: {
          name: data.label,
          description: data.description || null,
          totalPriceCents: totalApprox,
          parts: {
            deleteMany: {},
            create: buildPcBuildParts(entries, prices),
          },
        },
      });
    } else {
      // No linked PCBuild yet — create one now.
      const pcBuild = await tx.pCBuild.create({
        data: {
          name: data.label,
          description: data.description || null,
          status: 'finalized',
          totalPriceCents: totalApprox,
          parts: { create: buildPcBuildParts(entries, prices) },
        },
      });
      await tx.referenceBuild.update({ where: { id }, data: { pcBuildId: pcBuild.id } });
    }

    await tx.referenceBuild.update({
      where: { id },
      data: {
        buildKey: data.buildKey,
        label: data.label,
        description: data.description || null,
        isActive: data.isActive,
        maxResolution: data.maxResolution,
        totalApprox,
        parts: {
          deleteMany: {},
          create: entries.map((e, i) => ({
            partId: e.partId,
            component: e.component,
            sortOrder: i,
            approxPrice: prices.get(e.partId) ?? 0,
          })),
        },
      },
    });
  });

  revalidatePath('/reference-builds');
}

export async function deleteReferenceBuild(id: string) {
  const build = await db.referenceBuild.findUniqueOrThrow({
    where: { id },
    select: { pcBuildId: true },
  });

  await db.referenceBuild.delete({ where: { id } });

  if (build.pcBuildId) {
    await db.pCBuild.delete({ where: { id: build.pcBuildId } });
  }

  revalidatePath('/reference-builds');
}
