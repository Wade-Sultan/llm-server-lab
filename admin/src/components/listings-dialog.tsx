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
import { asinSchema } from '@/lib/utils';
import { createAmazonListing, updateAmazonListing, deleteListing, type AmazonListingFormData } from '@/lib/listings';

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

export function ListingsDialog({ partId, partName, listings }: { partId: string; partName: string; listings: ListingWithAmazon[] }) {
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
            addingType === 'ebay' ? (
              <div className="space-y-4">
                <p className="text-sm text-muted-foreground rounded-md border border-dashed p-4 text-center">
                  eBay listings: work in progress.
                </p>
                <div className="flex justify-end">
                  <Button variant="outline" onClick={() => setAddingType(null)}>Back</Button>
                </div>
              </div>
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
                    <TableHead className="w-0" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filtered.length === 0 && (
                    <TableRow>
                      <TableCell colSpan={4} className="text-center text-muted-foreground">
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
                      <TableCell>
                        <div className="flex items-center gap-1 justify-end">
                          {l.listingType === 'amazon' && (
                            <Button variant="ghost" size="sm" onClick={() => setEditingListing(l)}>
                              <Pencil className="h-3.5 w-3.5" />
                            </Button>
                          )}
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
