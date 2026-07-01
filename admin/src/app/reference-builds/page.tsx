export const dynamic = 'force-dynamic';

import { db } from '@/lib/prisma';
import { ReferenceBuildTable } from './client';

export default async function ReferenceBuildsPage() {
  const [builds, partOptions] = await Promise.all([
    db.referenceBuild.findMany({
      orderBy: { buildKey: 'asc' },
      include: {
        parts: {
          include: {
            part: { select: { id: true, name: true, partType: true, streetPriceCents: true } },
          },
        },
      },
    }),
    db.pcPart.findMany({
      where: {
        isActive: true,
        streetPriceCents: { not: null },
        listings: { some: {} },
      },
      select: { id: true, name: true, partType: true, streetPriceCents: true },
      orderBy: { name: 'asc' },
    }),
  ]);

  return <ReferenceBuildTable builds={builds} partOptions={partOptions} />;
}
