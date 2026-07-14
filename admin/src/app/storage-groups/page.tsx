export const dynamic = 'force-dynamic';

import { db } from '@/lib/prisma';
import { StorageGroupTable } from './client';

export default async function StorageGroupsPage() {
  const data = await db.storageGroup.findMany({ orderBy: { name: 'asc' } });
  return <StorageGroupTable data={data} />;
}
