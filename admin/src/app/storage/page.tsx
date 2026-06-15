export const dynamic = 'force-dynamic';

import { db } from '@/lib/prisma';
import { StorageTable } from './client';

export default async function StoragePage() {
  const data = await db.storage.findMany({
    include: { pcPart: true },
    orderBy: { pcPart: { name: 'asc' } },
  });
  return <StorageTable data={data} />;
}
