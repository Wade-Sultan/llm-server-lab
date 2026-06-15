export const dynamic = 'force-dynamic';

import { db } from '@/lib/prisma';
import { CaseTable } from './client';

export default async function CasesPage() {
  const data = await db.pcCase.findMany({
    include: { pcPart: true },
    orderBy: { pcPart: { name: 'asc' } },
  });
  return <CaseTable data={data} />;
}
