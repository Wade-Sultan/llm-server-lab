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
  // Non-grouped slots reference a specific part.
  cpuId: string | null;
  motherboardId: string | null;
  caseId: string | null;
  cpuCoolerId: string | null;
  fanIds: string[];
  // Grouped slots reference a group; a random member is frozen in at save.
  ramGroupId: string | null;
  psuGroupId: string | null;
  gpuChipsetIds: string[];
  storageGroupIds: string[];
}

type Entry = { partId: string; component: BuildComponentRole; priceCents: number };

const REQUIRED: Record<BuildComponentRole, boolean> = {
  cpu: true, cpucooler: true, motherboard: true,
  ram: true, storage: true, psu: true, case: true,
  gpu: false, fan: false,
};

// A group loaded with its active members + street price. Picking a member is
// what "select a group, freeze a random one at save" means; price comes from
// the group (every member is priced identically).
type GroupWithMembers = { streetPriceCents: number | null; variants: { id: string }[] } | null;

function pickMember(group: GroupWithMembers): { partId: string; priceCents: number } | null {
  if (!group || group.variants.length === 0) return null;
  const pick = group.variants[Math.floor(Math.random() * group.variants.length)];
  return { partId: pick.id, priceCents: group.streetPriceCents ?? 0 };
}

const memberSelect = { streetPriceCents: true, variants: { where: { pcPart: { isActive: true } }, select: { id: true } } };

async function resolveEntries(data: ReferenceBuildFormData): Promise<Entry[]> {
  const entries: Entry[] = [];

  // --- Non-grouped: price from pc_parts ---
  const partIds = [data.cpuId, data.motherboardId, data.caseId, data.cpuCoolerId, ...data.fanIds]
    .filter((x): x is string => !!x);
  const priceRows = partIds.length
    ? await db.pcPart.findMany({ where: { id: { in: partIds } }, select: { id: true, streetPriceCents: true } })
    : [];
  const partPrice = new Map(priceRows.map(p => [p.id, p.streetPriceCents ?? 0]));
  const addPart = (id: string | null, component: BuildComponentRole) => {
    if (id) entries.push({ partId: id, component, priceCents: partPrice.get(id) ?? 0 });
  };
  addPart(data.cpuId, 'cpu');
  addPart(data.motherboardId, 'motherboard');
  addPart(data.caseId, 'case');
  addPart(data.cpuCoolerId, 'cpucooler');
  for (const id of data.fanIds) addPart(id, 'fan');

  // --- Grouped: freeze a random member, price from the group ---
  const addGroup = (picked: { partId: string; priceCents: number } | null, component: BuildComponentRole) => {
    if (picked) entries.push({ ...picked, component });
  };
  if (data.ramGroupId) {
    addGroup(pickMember(await db.ramGroup.findUnique({ where: { id: data.ramGroupId }, select: memberSelect })), 'ram');
  }
  if (data.psuGroupId) {
    addGroup(pickMember(await db.psuGroup.findUnique({ where: { id: data.psuGroupId }, select: memberSelect })), 'psu');
  }
  for (const gid of data.gpuChipsetIds) {
    addGroup(pickMember(await db.gpuChipset.findUnique({ where: { id: gid }, select: memberSelect })), 'gpu');
  }
  for (const gid of data.storageGroupIds) {
    addGroup(pickMember(await db.storageGroup.findUnique({ where: { id: gid }, select: memberSelect })), 'storage');
  }
  return entries;
}

// pc_build_parts requires one-per-role; take the first occurrence of each component.
function pcBuildParts(entries: Entry[]) {
  const seen = new Set<string>();
  return entries
    .filter(e => { if (seen.has(e.component)) return false; seen.add(e.component); return true; })
    .map(e => ({
      partId: e.partId,
      role: e.component,
      requiredComponent: REQUIRED[e.component] ?? false,
      priceAtBuild: e.priceCents,
    }));
}

function refBuildParts(entries: Entry[]) {
  return entries.map((e, i) => ({
    partId: e.partId,
    component: e.component,
    sortOrder: i,
    approxPrice: e.priceCents,
  }));
}

export async function createReferenceBuild(data: ReferenceBuildFormData) {
  const entries = await resolveEntries(data);
  const totalApprox = entries.reduce((s, e) => s + e.priceCents, 0) || null;

  const pcBuild = await db.pCBuild.create({
    data: {
      name: data.label,
      description: data.description || null,
      status: 'finalized',
      totalPriceCents: totalApprox,
      parts: { create: pcBuildParts(entries) },
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
      parts: { create: refBuildParts(entries) },
    },
  });

  revalidatePath('/reference-builds');
}

export async function updateReferenceBuild(id: string, data: ReferenceBuildFormData) {
  // Every save re-resolves the grouped slots — i.e. re-rolls the frozen member.
  const entries = await resolveEntries(data);
  const totalApprox = entries.reduce((s, e) => s + e.priceCents, 0) || null;

  const existing = await db.referenceBuild.findUniqueOrThrow({ where: { id }, select: { pcBuildId: true } });

  await db.$transaction(async tx => {
    if (existing.pcBuildId) {
      await tx.pCBuild.update({
        where: { id: existing.pcBuildId },
        data: {
          name: data.label,
          description: data.description || null,
          totalPriceCents: totalApprox,
          parts: { deleteMany: {}, create: pcBuildParts(entries) },
        },
      });
    } else {
      const pcBuild = await tx.pCBuild.create({
        data: {
          name: data.label,
          description: data.description || null,
          status: 'finalized',
          totalPriceCents: totalApprox,
          parts: { create: pcBuildParts(entries) },
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
        parts: { deleteMany: {}, create: refBuildParts(entries) },
      },
    });
  });

  revalidatePath('/reference-builds');
}

export async function deleteReferenceBuild(id: string) {
  const build = await db.referenceBuild.findUniqueOrThrow({ where: { id }, select: { pcBuildId: true } });
  await db.referenceBuild.delete({ where: { id } });
  if (build.pcBuildId) {
    await db.pCBuild.delete({ where: { id: build.pcBuildId } });
  }
  revalidatePath('/reference-builds');
}
