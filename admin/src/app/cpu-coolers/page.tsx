export const dynamic = 'force-dynamic';

import { db } from '@/lib/prisma';
import { CpuCoolerTable } from './client';

export default async function CpuCoolersPage() {
  const data = await db.cpuCooler.findMany({
    include: { pcPart: { include: { listings: { include: { amazonListing: true } } } } },
    orderBy: { pcPart: { name: 'asc' } },
  });
  return <CpuCoolerTable data={data} />;
}
