import { db } from '@/lib/prisma';
import { DiscoveryClient, type SerializedRun } from './client';

export const dynamic = 'force-dynamic';

export default async function DiscoveryPage() {
  const [items, runs, chipsets] = await Promise.all([
    db.discoveredItem.findMany({
      where: { reviewStatus: 'pending' },
      orderBy: { createdAt: 'desc' },
    }),
    db.discoveryRun.findMany({ orderBy: { startedAt: 'desc' }, take: 20 }),
    db.gpuChipset.findMany({ orderBy: { name: 'asc' }, select: { id: true, name: true } }),
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

  // One id → name map covering both match kinds (chipset matches resolve
  // against the chipsets list already loaded for the approve form).
  const matchedNames: Record<string, string> = {};
  for (const p of matchedParts) matchedNames[p.id] = p.name;
  for (const c of chipsets) matchedNames[c.id] = c.name;

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
      matchedNames={matchedNames}
    />
  );
}
