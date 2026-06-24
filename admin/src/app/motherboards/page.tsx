export const dynamic = 'force-dynamic';

import { db } from '@/lib/prisma';
import { MotherboardTable } from './client';

export default async function MotherboardsPage() {
  const data = await db.motherboard.findMany({
    include: { pcPart: { include: { listings: { include: { amazonListing: true } } } } },
    orderBy: { pcPart: { name: 'asc' } },
  });
  return <MotherboardTable data={data} />;
}
