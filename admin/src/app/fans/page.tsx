import { db } from '@/lib/prisma';
import { FanTable } from './client';

export default async function FansPage() {
  const data = await db.fan.findMany({
    include: { pcPart: true },
    orderBy: { pcPart: { name: 'asc' } },
  });
  return <FanTable data={data} />;
}
