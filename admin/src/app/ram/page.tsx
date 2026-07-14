export const dynamic = 'force-dynamic';

import { db } from '@/lib/prisma';
import { RamTable } from './client';

export default async function RamPage() {
  const [data, groups] = await Promise.all([
    db.ramKit.findMany({
      include: {
        pcPart: { include: { listings: { include: { amazonListing: true } } } },
        group: true,
      },
      orderBy: { pcPart: { name: 'asc' } },
    }),
    db.ramGroup.findMany({ orderBy: { name: 'asc' }, select: { id: true, name: true } }),
  ]);
  return <RamTable data={data} groups={groups} />;
}
