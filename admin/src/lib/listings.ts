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

export async function deleteListing(listingId: string) {
  await db.listing.delete({ where: { id: listingId } });
  revalidatePath('/', 'layout');
}
