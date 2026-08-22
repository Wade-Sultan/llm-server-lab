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
async function resolveListingFailure(where: { partId: string } | { partId: { in: string[] } }) {
  try {
    await db.listingLookupFailure.updateMany({
      where: { ...where, resolvedAt: null },
      data: { resolvedAt: new Date() },
    });
  } catch (err) {
    console.error('failed to resolve listing failure', { where, err });
  }
}

/**
 * What a listing is attached to: one part, or one of the four part groups the
 * catalog models. Exactly one, enforced by ck_listings_one_target in the
 * database (Alembic e5a7c9b1d3f5).
 *
 * A group target exists because an eBay listing is a filtered search URL, and
 * one "RTX 3090" search is right for every partner board of that chipset —
 * entering it per board means re-entering it and missing boards added later.
 */
export type ListingTarget =
  | { kind: 'part'; id: string }
  | { kind: 'gpuChipset'; id: string }
  | { kind: 'psuGroup'; id: string }
  | { kind: 'ramGroup'; id: string }
  | { kind: 'storageGroup'; id: string };

/** The target as Prisma column data — one key set, four left undefined. */
function targetData(target: ListingTarget) {
  return {
    partId: target.kind === 'part' ? target.id : null,
    gpuChipsetId: target.kind === 'gpuChipset' ? target.id : null,
    psuGroupId: target.kind === 'psuGroup' ? target.id : null,
    ramGroupId: target.kind === 'ramGroup' ? target.id : null,
    storageGroupId: target.kind === 'storageGroup' ? target.id : null,
  };
}

/**
 * Every part the target covers, for closing failure rows: the part itself, or
 * all current members of the group. Members added later are covered by the
 * listing but were never in a failure row to begin with, so there is nothing
 * to close for them.
 */
async function partsCoveredBy(target: ListingTarget): Promise<string[]> {
  switch (target.kind) {
    case 'part':
      return [target.id];
    case 'gpuChipset':
      return (await db.gpu.findMany({ where: { gpuChipsetId: target.id }, select: { id: true } })).map((p) => p.id);
    case 'psuGroup':
      return (await db.psu.findMany({ where: { psuGroupId: target.id }, select: { id: true } })).map((p) => p.id);
    case 'ramGroup':
      return (await db.ramKit.findMany({ where: { ramGroupId: target.id }, select: { id: true } })).map((p) => p.id);
    case 'storageGroup':
      return (await db.storageDrive.findMany({ where: { storageGroupId: target.id }, select: { id: true } })).map((p) => p.id);
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
  await resolveListingFailure({ partId });
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
//
// The target may be a part or a group. Amazon deliberately has no group form:
// an Amazon listing is an ASIN, which identifies one specific board, so
// attaching one to a whole chipset would send every buyer of every variant to
// the same wrong product page. A search URL has no such problem.
export async function createEbayListing(target: ListingTarget, data: EbayListingFormData) {
  await db.listing.create({
    data: {
      ...targetData(target),
      listingType: 'ebay',
      marketplace: 'ebay',
      url: data.url.trim(),
    },
  });
  await resolveListingFailure({ partId: { in: await partsCoveredBy(target) } });
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
