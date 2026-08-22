'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import type { AmazonListing, Listing } from '@prisma/client';
import { Pencil, Plus, Tag, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { asinSchema, ebayUrlSchema } from '@/lib/utils';
import { Checkbox } from '@/components/ui/checkbox';
import {
  createAmazonListing, updateAmazonListing, createEbayListing, updateEbayListing, deleteListing,
  type AmazonListingFormData, type EbayListingFormData, type ListingTarget,
} from '@/lib/listings';

/**
 * The group this part belongs to, when it has one — a GPU's chipset, a PSU's
 * group, and so on. Present only for the four part types the catalog groups;
 * absent for a CPU or a case, which are one-of-a-kind.
 */
export type PartGroup = {
  kind: Exclude<ListingTarget['kind'], 'part'>;
  id: string;
  name: string;
};

type ListingWithAmazon = Listing & { amazonListing: AmazonListing | null };

type MarketplaceFilter = 'all' | 'amazon' | 'ebay';

const amazonSchema = z.object({
  asin: asinSchema,
  brand: z.string(),
});

function AmazonListingForm({
  listing,
  partId,
  onSuccess,
  onCancel,
}: {
  listing: ListingWithAmazon | null;
  partId: string;
  onSuccess: () => void;
  onCancel: () => void;
}) {
  const form = useForm<AmazonListingFormData>({
    resolver: zodResolver(amazonSchema),
    defaultValues: {
      asin: listing?.amazonListing?.asin ?? '',
      brand: listing?.amazonListing?.brand ?? '',
    },
  });
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(data: AmazonListingFormData) {
    setError(null);
    try {
      if (listing) {
        await updateAmazonListing(listing.id, data);
      } else {
        await createAmazonListing(partId, data);
      }
      onSuccess();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'An error occurred');
    }
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
        <FormField control={form.control} name="asin"
          render={({ field }) => (
            <FormItem>
              <FormLabel>ASIN</FormLabel>
              <FormControl><Input {...field} placeholder="B0XXXXXXXX" maxLength={10} /></FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField control={form.control} name="brand"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Brand</FormLabel>
              <FormControl><Input {...field} placeholder="e.g. ASUS, MSI, Gigabyte" /></FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        {error && <p className="text-sm text-destructive">{error}</p>}
        <div className="flex justify-end gap-2 pt-2">
          <Button type="button" variant="outline" onClick={onCancel}>Cancel</Button>
          <Button type="submit" disabled={form.formState.isSubmitting}>
            {form.formState.isSubmitting ? 'Saving...' : listing ? 'Update Listing' : 'Add Listing'}
          </Button>
        </div>
      </form>
    </Form>
  );
}

const ebaySchema = z.object({
  url: ebayUrlSchema,
});

function EbayListingForm({
  listing,
  partId,
  group,
  onSuccess,
  onCancel,
}: {
  listing: ListingWithAmazon | null;
  partId: string;
  group?: PartGroup;
  onSuccess: () => void;
  onCancel: () => void;
}) {
  const form = useForm<EbayListingFormData>({
    resolver: zodResolver(ebaySchema),
    defaultValues: {
      url: listing?.url ?? '',
    },
  });
  const [error, setError] = useState<string | null>(null);
  // Off by default: attaching to the group is the broader claim, so it should
  // be a thing you chose rather than a thing you failed to notice.
  const [applyToGroup, setApplyToGroup] = useState(false);

  async function onSubmit(data: EbayListingFormData) {
    setError(null);
    try {
      if (listing) {
        await updateEbayListing(listing.id, data);
      } else {
        const target: ListingTarget =
          applyToGroup && group ? { kind: group.kind, id: group.id } : { kind: 'part', id: partId };
        await createEbayListing(target, data);
      }
      onSuccess();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'An error occurred');
    }
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
        <FormField control={form.control} name="url"
          render={({ field }) => (
            <FormItem>
              <FormLabel>eBay search URL</FormLabel>
              <FormControl><Input {...field} placeholder="https://www.ebay.com/sch/i.html?_nkw=..." /></FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        {/* Only when creating: an existing listing's target is fixed, and the
            edit form only changes the URL. */}
        {group && !listing && (
          <div className="flex items-start gap-2 rounded-md border p-3">
            <Checkbox
              id="apply-to-group"
              checked={applyToGroup}
              onCheckedChange={(v) => setApplyToGroup(v === true)}
              className="mt-0.5"
            />
            <div className="space-y-1">
              <label htmlFor="apply-to-group" className="text-sm font-medium leading-none cursor-pointer">
                Use for all of {group.name}
              </label>
              <p className="text-xs text-muted-foreground">
                Applies to every variant of {group.name}, including ones added later.
                A variant with its own eBay listing keeps it.
              </p>
            </div>
          </div>
        )}
        <p className="text-xs text-muted-foreground">
          Build a filtered search on eBay and paste the resulting URL. Affiliate tracking (your eBay Partner
          Network campaign) is appended automatically when the link is served.
        </p>
        {error && <p className="text-sm text-destructive">{error}</p>}
        <div className="flex justify-end gap-2 pt-2">
          <Button type="button" variant="outline" onClick={onCancel}>Cancel</Button>
          <Button type="submit" disabled={form.formState.isSubmitting}>
            {form.formState.isSubmitting ? 'Saving...' : listing ? 'Update Listing' : 'Add Listing'}
          </Button>
        </div>
      </form>
    </Form>
  );
}

export function ListingsDialog({ partId, partName, group, listings }: { partId: string; partName: string; group?: PartGroup; listings: ListingWithAmazon[] }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState<MarketplaceFilter>('all');
  const [editingListing, setEditingListing] = useState<ListingWithAmazon | null>(null);
  const [addingType, setAddingType] = useState<'amazon' | 'ebay' | null>(null);
  const [deleteId, setDeleteId] = useState<string | null>(null);

  const filtered = listings.filter((l) => filter === 'all' || l.marketplace === filter);
  const showingForm = addingType !== null || editingListing !== null;

  const handleSuccess = () => {
    setAddingType(null);
    setEditingListing(null);
    router.refresh();
  };

  const handleDelete = async (id: string) => {
    await deleteListing(id);
    setDeleteId(null);
    router.refresh();
  };

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        <Tag className="h-3.5 w-3.5 mr-1.5" />
        Listings{listings.length > 0 ? ` (${listings.length})` : ''}
      </Button>
      <Dialog open={open} onOpenChange={(o) => { setOpen(o); if (!o) { setAddingType(null); setEditingListing(null); } }}>
        <DialogContent className="max-w-2xl">
          <DialogHeader><DialogTitle>Listings — {partName}</DialogTitle></DialogHeader>

          {showingForm ? (
            addingType === 'ebay' || editingListing?.marketplace === 'ebay' ? (
              <EbayListingForm
                listing={editingListing}
                partId={partId}
                group={group}
                onSuccess={handleSuccess}
                onCancel={() => { setAddingType(null); setEditingListing(null); }}
              />
            ) : (
              <AmazonListingForm
                listing={editingListing}
                partId={partId}
                onSuccess={handleSuccess}
                onCancel={() => { setAddingType(null); setEditingListing(null); }}
              />
            )
          ) : (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <div className="flex gap-1">
                  {([
                    ['all', 'All'], ['amazon', 'Amazon'], ['ebay', 'eBay'],
                  ] as [MarketplaceFilter, string][]).map(([value, label]) => (
                    <Button
                      key={value}
                      size="sm"
                      variant={filter === value ? 'default' : 'outline'}
                      onClick={() => setFilter(value)}
                    >
                      {label}
                    </Button>
                  ))}
                </div>
                <div className="flex gap-1">
                  <Button size="sm" onClick={() => setAddingType('amazon')}>
                    <Plus className="h-3.5 w-3.5 mr-1" />Amazon
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => setAddingType('ebay')}>
                    <Plus className="h-3.5 w-3.5 mr-1" />eBay
                  </Button>
                </div>
              </div>

              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Marketplace</TableHead>
                    <TableHead>ASIN</TableHead>
                    <TableHead>Brand</TableHead>
                    <TableHead>URL</TableHead>
                    <TableHead className="w-0" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filtered.length === 0 && (
                    <TableRow>
                      <TableCell colSpan={5} className="text-center text-muted-foreground">
                        No listings yet.
                      </TableCell>
                    </TableRow>
                  )}
                  {filtered.map((l) => (
                    <TableRow key={l.id}>
                      <TableCell>
                        <Badge variant="secondary" className="capitalize">{l.marketplace}</Badge>
                      </TableCell>
                      <TableCell>{l.amazonListing?.asin ?? '—'}</TableCell>
                      <TableCell>{l.amazonListing?.brand ?? '—'}</TableCell>
                      <TableCell className="max-w-[220px]">
                        {l.url ? (
                          <a href={l.url} target="_blank" rel="noreferrer" className="block truncate text-primary hover:underline" title={l.url}>
                            {l.url}
                          </a>
                        ) : '—'}
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-1 justify-end">
                          <Button variant="ghost" size="sm" onClick={() => setEditingListing(l)}>
                            <Pencil className="h-3.5 w-3.5" />
                          </Button>
                          <Button variant="ghost" size="sm" className="text-destructive hover:text-destructive" onClick={() => setDeleteId(l.id)}>
                            <Trash2 className="h-3.5 w-3.5" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </DialogContent>
      </Dialog>
      <AlertDialog open={!!deleteId} onOpenChange={(o) => !o && setDeleteId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete listing?</AlertDialogTitle>
            <AlertDialogDescription>This action cannot be undone.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => deleteId && handleDelete(deleteId)}>Delete</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
