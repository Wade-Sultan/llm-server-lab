import { db } from '@/lib/prisma';
import { MotherboardTable } from './client';

export default async function MotherboardsPage() {
  const data = await db.motherboard.findMany({
    include: { pcPart: true },
    orderBy: { pcPart: { name: 'asc' } },
  });
  return <MotherboardTable data={data} />;
}
