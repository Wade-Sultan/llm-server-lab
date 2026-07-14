export const dynamic = 'force-dynamic';

import { db } from '@/lib/prisma';
import { GpuChipsetTable } from './client';

export default async function GpuChipsetsPage() {
  const data = await db.gpuChipset.findMany({ orderBy: { name: 'asc' } });
  return <GpuChipsetTable data={data} />;
}
