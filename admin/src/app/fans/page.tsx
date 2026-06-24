export const dynamic = 'force-dynamic';

import { db } from '@/lib/prisma';
import { FanTable } from './client';

export default async function FansPage() {
  const data = await db.fan.findMany({
    include: { pcPart: { include: { listings: { include: { amazonListing: true } } } } },
    orderBy: { pcPart: { name: 'asc' } },
  });
  return <FanTable data={data} />;
}
