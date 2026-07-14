export const dynamic = 'force-dynamic';

import { db } from '@/lib/prisma';
import { StorageTable } from './client';

export default async function StoragePage() {
  const [data, groups] = await Promise.all([
    db.storageDrive.findMany({
      include: {
        pcPart: { include: { listings: { include: { amazonListing: true } } } },
        group: true,
      },
      orderBy: { pcPart: { name: 'asc' } },
    }),
    db.storageGroup.findMany({ orderBy: { name: 'asc' }, select: { id: true, name: true } }),
  ]);
  return <StorageTable data={data} groups={groups} />;
}
