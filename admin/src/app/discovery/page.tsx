import { db } from '@/lib/prisma';
import { DiscoveryClient, type GroupOptions, type SerializedRun } from './client';

export const dynamic = 'force-dynamic';

const byName = { orderBy: { name: 'asc' }, select: { id: true, name: true } } as const;

export default async function DiscoveryPage() {
  const [items, runs, chipsets, ramGroups, storageGroups, psuGroups] = await Promise.all([
    db.discoveredItem.findMany({
      where: { reviewStatus: 'pending' },
      orderBy: { createdAt: 'desc' },
    }),
    db.discoveryRun.findMany({ orderBy: { startedAt: 'desc' }, take: 20 }),
    db.gpuChipset.findMany(byName),
    // Offered to RAM/storage/PSU approvals so a discovered SKU can join an
    // existing spec group instead of minting a near-duplicate of it.
    db.ramGroup.findMany(byName),
    db.storageGroup.findMany(byName),
    db.psuGroup.findMany(byName),
  ]);

  const matchedPartIds = items
    .map((i) => i.matchedPartId)
    .filter((id): id is string => id !== null);
  const matchedParts = matchedPartIds.length
    ? await db.pcPart.findMany({
        where: { id: { in: matchedPartIds } },
        select: { id: true, name: true },
      })
    : [];

  const matchedAiModelIds = items
    .map((i) => i.matchedAiModelId)
    .filter((id): id is string => id !== null);
  const matchedAiModels = matchedAiModelIds.length
    ? await db.aiModel.findMany({
        where: { id: { in: matchedAiModelIds } },
        select: { id: true, name: true },
      })
    : [];

  // One id → name map covering all three match kinds (chipset matches resolve
  // against the chipsets list already loaded for the approve form).
  const matchedNames: Record<string, string> = {};
  for (const p of matchedParts) matchedNames[p.id] = p.name;
  for (const c of chipsets) matchedNames[c.id] = c.name;
  for (const m of matchedAiModels) matchedNames[m.id] = m.name;

  const groups: GroupOptions = {
    ram: ramGroups,
    storage: storageGroups,
    psu: psuGroups,
  };

  // Prisma Decimal isn't serializable across the RSC boundary.
  const serializedRuns: SerializedRun[] = runs.map((run) => ({
    ...run,
    totalCostUsd: run.totalCostUsd?.toNumber() ?? null,
  }));

  return (
    <DiscoveryClient
      items={items}
      runs={serializedRuns}
      chipsets={chipsets}
      groups={groups}
      matchedNames={matchedNames}
    />
  );
}
