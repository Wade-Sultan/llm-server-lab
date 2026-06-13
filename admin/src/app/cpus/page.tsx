import { db } from '@/lib/prisma';
import { CpuTable } from './client';

export default async function CpusPage() {
  const data = await db.cpu.findMany({
    include: { pcPart: true },
    orderBy: { pcPart: { name: 'asc' } },
  });
  return <CpuTable data={data} />;
}
