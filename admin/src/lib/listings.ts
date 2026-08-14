'use server';

import { revalidatePath } from 'next/cache';
import { db } from '@/lib/prisma';

/**
 * Close any open listing-failure row for a part.
 *
 * Commerce does this itself when a listing is created through its API
 * (internal/server/listings_handlers.go), but admin writes listings straight
 * through Prisma — which is how they actually get created in practice — so the
 * same close has to happen here or a gap someone just fixed would stay open on
 * the failures page and in tomorrow's digest.
 *
 * Best-effort and deliberately not awaited into the caller's error path: an
 * added listing must not fail because bookkeeping about it did.
 */
async function resolveListingFailure(partId: string) {
  try {
    await db.listingLookupFailure.updateMany({
      where: { partId, resolvedAt: null },
      data: { resolvedAt: new Date() },
    });
  } catch (err) {
    console.error('failed to resolve listing failure', { partId, err });
  }
}

export interface AmazonListingFormData {
  asin: string;
  brand: string;
}

export async function createAmazonListing(partId: string, data: AmazonListingFormData) {
  await db.listing.create({
    data: {
      partId,
      listingType: 'amazon',
      marketplace: 'amazon',
      amazonListing: {
        create: {
          asin: data.asin.trim().toUpperCase(),
          brand: data.brand.trim() || null,
        },
      },
    },
  });
  await resolveListingFailure(partId);
  revalidatePath('/', 'layout');
}

export async function updateAmazonListing(listingId: string, data: AmazonListingFormData) {
  await db.amazonListing.update({
    where: { id: listingId },
    data: {
      asin: data.asin.trim().toUpperCase(),
      brand: data.brand.trim() || null,
    },
  });
  revalidatePath('/', 'layout');
}

export interface EbayListingFormData {
  url: string;
}

// eBay listings are stored as a base `listings` row (no marketplace subtype
// row): just the filtered search URL. Commerce appends EPN affiliate tracking
// when it serves the listing, the same read-time model as Amazon's ASIN → URL.
export async function createEbayListing(partId: string, data: EbayListingFormData) {
  await db.listing.create({
    data: {
      partId,
      listingType: 'ebay',
      marketplace: 'ebay',
      url: data.url.trim(),
    },
  });
  await resolveListingFailure(partId);
  revalidatePath('/', 'layout');
}

export async function updateEbayListing(listingId: string, data: EbayListingFormData) {
  await db.listing.update({
    where: { id: listingId },
    data: { url: data.url.trim() },
  });
  revalidatePath('/', 'layout');
}

export async function deleteListing(listingId: string) {
  await db.listing.delete({ where: { id: listingId } });
  revalidatePath('/', 'layout');
}
