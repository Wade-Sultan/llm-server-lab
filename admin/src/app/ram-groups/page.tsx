export const dynamic = 'force-dynamic';

import { db } from '@/lib/prisma';
import { RamGroupTable } from './client';

export default async function RamGroupsPage() {
  const data = await db.ramGroup.findMany({ orderBy: { name: 'asc' } });
  return <RamGroupTable data={data} />;
}
