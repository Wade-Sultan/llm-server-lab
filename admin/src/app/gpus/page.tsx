export const dynamic = 'force-dynamic';

import { db } from '@/lib/prisma';
import { GpuTable } from './client';

export default async function GpusPage() {
  const [data, chipsets] = await Promise.all([
    db.gpu.findMany({
      include: {
        pcPart: { include: { listings: { include: { amazonListing: true } } } },
        chipset: true,
      },
      orderBy: { pcPart: { name: 'asc' } },
    }),
    db.gpuChipset.findMany({ orderBy: { name: 'asc' }, select: { id: true, name: true } }),
  ]);
  return <GpuTable data={data} chipsets={chipsets} />;
}
