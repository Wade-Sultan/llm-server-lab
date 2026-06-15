export const dynamic = 'force-dynamic';

import { db } from '@/lib/prisma';
import { UserTable } from './client';

export default async function UsersPage() {
  const data = await db.user.findMany({ orderBy: { createdAt: 'desc' } });
  return <UserTable data={data} />;
}
