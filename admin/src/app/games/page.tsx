export const dynamic = 'force-dynamic';

import { db } from '@/lib/prisma';
import { GamesTable } from './client';

export default async function GamesPage() {
  const [games, partOptions] = await Promise.all([
    db.game.findMany({
      orderBy: { title: 'asc' },
      include: {
        minimumParts: {
          orderBy: [{ tier: 'asc' }, { role: 'asc' }],
          include: { part: { select: { id: true, name: true } } },
        },
      },
    }),
    // Minimum-spec rows link to a CPU or GPU part.
    db.pcPart.findMany({
      where: { partType: { in: ['cpu', 'gpu'] } },
      select: { id: true, name: true, partType: true },
      orderBy: { name: 'asc' },
    }),
  ]);

  return <GamesTable games={games} partOptions={partOptions} />;
}
