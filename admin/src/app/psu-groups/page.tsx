export const dynamic = 'force-dynamic';

import { db } from '@/lib/prisma';
import { PsuGroupTable } from './client';

export default async function PsuGroupsPage() {
  const data = await db.psuGroup.findMany({ orderBy: { name: 'asc' } });
  return <PsuGroupTable data={data} />;
}
