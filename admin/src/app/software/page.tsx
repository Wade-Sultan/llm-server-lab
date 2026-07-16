export const dynamic = 'force-dynamic';

import { db } from '@/lib/prisma';
import { SoftwareTable } from './client';

export default async function SoftwarePage() {
  const software = await db.software.findMany({
    orderBy: { name: 'asc' },
    include: { tiers: { orderBy: { sortOrder: 'asc' } } },
  });

  return <SoftwareTable software={software} />;
}
