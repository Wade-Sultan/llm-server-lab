export const dynamic = 'force-dynamic';

import { db } from '@/lib/prisma';
import { ListingFailuresTable, type FailureRow } from './client';

// Parts the listings API could not produce a listing for.
//
// Written by commerce on its read path (internal/store/listing_failures.go),
// keyed by part so one broken part is one row however many times the build
// card asked for it. Open rows first, worst first inside that: one part failing
// 400 times is a different problem from forty parts failing once.
export default async function ListingFailuresPage() {
  const failures = await db.listingLookupFailure.findMany({
    orderBy: [{ resolvedAt: 'asc' }, { occurrences: 'desc' }, { firstSeenAt: 'asc' }],
    // Resolved rows are kept for the history ("this part had no listing for
    // three weeks"), but they're not what the page is for, so the tail is
    // capped rather than growing without bound.
    take: 500,
    include: {
      part: {
        select: { id: true, name: true, partType: true, isActive: true },
      },
    },
  });

  const rows: FailureRow[] = failures.map((f) => ({
    partId: f.partId,
    partName: f.part.name,
    partType: f.part.partType,
    partIsActive: f.part.isActive,
    reason: f.reason,
    detail: f.detail,
    occurrences: f.occurrences,
    firstSeenAt: f.firstSeenAt.toISOString(),
    lastSeenAt: f.lastSeenAt.toISOString(),
    notifiedAt: f.notifiedAt?.toISOString() ?? null,
    resolvedAt: f.resolvedAt?.toISOString() ?? null,
  }));

  return <ListingFailuresTable rows={rows} />;
}
