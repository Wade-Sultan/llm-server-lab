export const dynamic = 'force-dynamic';

import { db } from '@/lib/prisma';
import { ReferenceBuildTable } from './client';

const groupSelect = { orderBy: { name: 'asc' as const }, select: { id: true, name: true, streetPriceCents: true } };

export default async function ReferenceBuildsPage() {
  const [builds, partOptions, gpuChipsets, ramGroups, storageGroups, psuGroups] = await Promise.all([
    db.referenceBuild.findMany({
      orderBy: { buildKey: 'asc' },
      include: {
        parts: {
          include: {
            part: {
              select: {
                id: true, name: true, partType: true,
                // group FKs so editing can pre-select the group a frozen member belongs to
                gpu: { select: { gpuChipsetId: true } },
                ramKit: { select: { ramGroupId: true } },
                storageDrive: { select: { storageGroupId: true } },
                psu: { select: { psuGroupId: true } },
              },
            },
          },
        },
      },
    }),
    // Non-grouped slots pick a specific part (must be priced + listed).
    db.pcPart.findMany({
      where: {
        isActive: true,
        streetPriceCents: { not: null },
        listings: { some: {} },
        partType: { in: ['cpu', 'motherboard', 'case', 'cpucooler', 'fan'] },
      },
      select: { id: true, name: true, partType: true, streetPriceCents: true },
      orderBy: { name: 'asc' },
    }),
    db.gpuChipset.findMany(groupSelect),
    db.ramGroup.findMany(groupSelect),
    db.storageGroup.findMany(groupSelect),
    db.psuGroup.findMany(groupSelect),
  ]);

  return (
    <ReferenceBuildTable
      builds={builds}
      partOptions={partOptions}
      gpuChipsets={gpuChipsets}
      ramGroups={ramGroups}
      storageGroups={storageGroups}
      psuGroups={psuGroups}
    />
  );
}
