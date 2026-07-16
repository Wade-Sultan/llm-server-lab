export const dynamic = 'force-dynamic';

import { db } from '@/lib/prisma';
import { AiModelsTable } from './client';

export default async function AiModelsPage() {
  const models = await db.aiModel.findMany({
    orderBy: { name: 'asc' },
    include: { workloads: { orderBy: { sortOrder: 'asc' } } },
  });

  return <AiModelsTable models={models} />;
}
