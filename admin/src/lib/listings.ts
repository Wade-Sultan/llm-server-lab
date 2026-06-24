import { db } from '@/lib/prisma';

export async function upsertAmazonAsin(partId: string, asin: string) {
  const trimmed = asin.trim().toUpperCase();
  const existing = await db.listing.findFirst({
    where: { partId, listingType: 'amazon' },
  });

  if (!trimmed) {
    if (existing) await db.listing.delete({ where: { id: existing.id } });
    return;
  }

  if (existing) {
    await db.amazonListing.update({ where: { id: existing.id }, data: { asin: trimmed } });
  } else {
    await db.listing.create({
      data: {
        partId,
        listingType: 'amazon',
        marketplace: 'amazon',
        amazonListing: { create: { asin: trimmed } },
      },
    });
  }
}
