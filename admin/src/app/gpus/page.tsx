export const dynamic = 'force-dynamic';

import { db } from '@/lib/prisma';
import { GpuTable } from './client';

export default async function GpusPage() {
  const data = await db.gpu.findMany({
    include: { pcPart: true },
    orderBy: { pcPart: { name: 'asc' } },
  });
  return <GpuTable data={data} />;
}
