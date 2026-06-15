export const dynamic = 'force-dynamic';

import { db } from '@/lib/prisma';
import { PsuTable } from './client';

export default async function PsusPage() {
  const data = await db.psu.findMany({
    include: { pcPart: true },
    orderBy: { pcPart: { name: 'asc' } },
  });
  return <PsuTable data={data} />;
}
