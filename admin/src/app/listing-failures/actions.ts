'use server';

import { revalidatePath } from 'next/cache';
import { db } from '@/lib/prisma';

/**
 * Close a failure by hand.
 *
 * Normally resolution is automatic: adding or reactivating a listing for the
 * part clears it (see resolveListingFailure in @/lib/listings, and commerce's
 * own createListing handler). This is for the cases automation can't judge —
 * a part that is genuinely unbuyable and should be deactivated instead, or a
 * lookup_error from an incident that is long over.
 *
 * Resolved rather than deleted, like every other close: firstSeenAt is the
 * useful history and deleting the row would lose it.
 */
export async function resolveFailure(partId: string) {
  await db.listingLookupFailure.updateMany({
    where: { partId, resolvedAt: null },
    data: { resolvedAt: new Date() },
  });
  revalidatePath('/listing-failures');
}

/**
 * Reopen a row closed too eagerly. Clears notifiedAt as well, so the next
 * digest reports it again — reopening it silently would leave it visible only
 * to whoever thought to look at this page.
 */
export async function reopenFailure(partId: string) {
  await db.listingLookupFailure.updateMany({
    where: { partId },
    data: { resolvedAt: null, notifiedAt: null },
  });
  revalidatePath('/listing-failures');
}
