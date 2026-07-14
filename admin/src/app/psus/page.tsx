export const dynamic = 'force-dynamic';

import { db } from '@/lib/prisma';
import { PsuTable } from './client';

export default async function PsusPage() {
  const [data, groups] = await Promise.all([
    db.psu.findMany({
      include: {
        pcPart: { include: { listings: { include: { amazonListing: true } } } },
        group: true,
      },
      orderBy: { pcPart: { name: 'asc' } },
    }),
    db.psuGroup.findMany({ orderBy: { name: 'asc' }, select: { id: true, name: true } }),
  ]);
  return <PsuTable data={data} groups={groups} />;
}
