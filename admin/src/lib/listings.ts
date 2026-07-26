'use server';

import { revalidatePath } from 'next/cache';
import { db } from '@/lib/prisma';

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
