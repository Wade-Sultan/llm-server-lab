export const dynamic = 'force-dynamic';

import { db } from '@/lib/prisma';
import { ReferenceBuildTable } from './client';

export default async function ReferenceBuildsPage() {
  const data = await db.referenceBuild.findMany({ orderBy: { buildKey: 'asc' } });
  return <ReferenceBuildTable data={data} />;
}
