export const dynamic = 'force-dynamic';

import { db } from '@/lib/prisma';
import { RamTable } from './client';

export default async function RamPage() {
  const data = await db.ram.findMany({
    include: { pcPart: { include: { listings: { include: { amazonListing: true } } } } },
    orderBy: { pcPart: { name: 'asc' } },
  });
  return <RamTable data={data} />;
}
