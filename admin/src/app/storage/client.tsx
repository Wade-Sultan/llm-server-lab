'use client';

import { useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import type { ColumnDef } from '@tanstack/react-table';
import type { Storage, PcPart, Listing, AmazonListing } from '@prisma/client';
import { Pencil, Trash2 } from 'lucide-react';
import { DataTable } from '@/components/data-table';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog';
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { Checkbox } from '@/components/ui/checkbox';
import { centsToUsd, formatUsd, asinSchema, getAmazonAsin } from '@/lib/utils';
import { createStorage, updateStorage, deleteStorage, type StorageFormData } from './actions';

type StorageWithPart = Storage & { pcPart: PcPart & { listings: (Listing & { amazonListing: AmazonListing | null })[] } };

const schema = z.object({
  name: z.string().min(1), manufacturer: z.string(), modelNumber: z.string(),
  yearReleased: z.coerce.number().int().nullable(), isActive: z.boolean(),
  streetPriceUsd: z.coerce.number().nullable(),
  asin: asinSchema,
  storageType: z.string(), formFactor: z.string(), interface: z.string(),
  capacityGb: z.coerce.number().int().nullable(), readSpeedMbps: z.coerce.number().int().nullable(),
  writeSpeedMbps: z.coerce.number().int().nullable(), hasDramCache: z.boolean(),
  enduranceTbw: z.coerce.number().int().nullable(), rpm: z.coerce.number().int().nullable(),
});

function StorageForm({ item, onSuccess }: { item: StorageWithPart | null; onSuccess: () => void }) {
  const form = useForm<StorageFormData>({
    resolver: zodResolver(schema),
    defaultValues: item ? {
      name: item.pcPart.name, manufacturer: item.pcPart.manufacturer ?? '',
      modelNumber: item.pcPart.modelNumber ?? '', yearReleased: item.pcPart.yearReleased,
      isActive: item.pcPart.isActive, streetPriceUsd: centsToUsd(item.pcPart.streetPriceCents),
      asin: getAmazonAsin(item.pcPart.listings),
      storageType: item.storageType ?? '',
      formFactor: item.formFactor ?? '', interface: item.interface ?? '',
      capacityGb: item.capacityGb, readSpeedMbps: item.readSpeedMbps,
      writeSpeedMbps: item.writeSpeedMbps, hasDramCache: item.hasDramCache,
      enduranceTbw: item.enduranceTbw, rpm: item.rpm,
    } : {
      name: '', manufacturer: '', modelNumber: '', yearReleased: null, isActive: true,
      streetPriceUsd: null,
      asin: '',
      storageType: '', formFactor: '', interface: '', capacityGb: null,
      readSpeedMbps: null, writeSpeedMbps: null, hasDramCache: false,
      enduranceTbw: null, rpm: null,
    },
  });

  const [error, setError] = useState<string | null>(null);
  const numChange = (onChange: (v: number | null) => void) => (e: React.ChangeEvent<HTMLInputElement>) =>
    onChange(e.target.value === '' ? null : Number(e.target.value));

  async function onSubmit(data: StorageFormData) {
    setError(null);
    try {
      if (item) { await updateStorage(item.id, data); } else { await createStorage(data); }
      onSuccess();
    } catch (e) { setError(e instanceof Error ? e.message : 'An error occurred'); }
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          {([ ['name','Name *'], ['manufacturer','Manufacturer'], ['modelNumber','Model Number'],
              ['storageType','Type (SSD/HDD)'], ['formFactor','Form Factor'], ['interface','Interface'],
          ] as [keyof StorageFormData, string][]).map(([name, label]) => (
            <FormField key={name} control={form.control} name={name}
              render={({ field }) => (
                <FormItem><FormLabel>{label}</FormLabel>
                  <FormControl><Input {...field} value={field.value as string ?? ''} /></FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          ))}
          {([ ['yearReleased','Year'], ['capacityGb','Capacity (GB)'], ['readSpeedMbps','Read (MB/s)'],
              ['writeSpeedMbps','Write (MB/s)'], ['enduranceTbw','Endurance (TBW)'], ['rpm','RPM'],
          ] as [keyof StorageFormData, string][]).map(([name, label]) => (
            <FormField key={name} control={form.control} name={name}
              render={({ field }) => (
                <FormItem><FormLabel>{label}</FormLabel>
                  <FormControl>
                    <Input type="number" value={(field.value as number | null) ?? ''} onChange={numChange(field.onChange)} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          ))}
          <FormField control={form.control} name="streetPriceUsd"
            render={({ field }) => (
              <FormItem><FormLabel>Street Price (USD)</FormLabel>
                <FormControl>
                  <Input type="number" step="0.01" value={(field.value as number | null) ?? ''} onChange={numChange(field.onChange)} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField control={form.control} name="asin"
            render={({ field }) => (
              <FormItem><FormLabel>Amazon ASIN</FormLabel>
                <FormControl><Input {...field} placeholder="B0XXXXXXXX" maxLength={10} /></FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
        </div>
        <div className="flex gap-6">
          {([ ['hasDramCache','DRAM Cache'], ['isActive','Active'] ] as [keyof StorageFormData, string][]).map(([name, label]) => (
            <FormField key={name} control={form.control} name={name}
              render={({ field }) => (
                <FormItem className="flex items-center gap-2 space-y-0">
                  <FormControl><Checkbox checked={field.value as boolean} onCheckedChange={field.onChange} /></FormControl>
                  <FormLabel>{label}</FormLabel>
                </FormItem>
              )}
            />
          ))}
        </div>
        {error && <p className="text-sm text-destructive">{error}</p>}
        <div className="flex justify-end pt-2">
          <Button type="submit" disabled={form.formState.isSubmitting}>
            {form.formState.isSubmitting ? 'Saving...' : item ? 'Update' : 'Create'}
          </Button>
        </div>
      </form>
    </Form>
  );
}

export function StorageTable({ data }: { data: StorageWithPart[] }) {
  const router = useRouter();
  const [selected, setSelected] = useState<StorageWithPart | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const [, startTransition] = useTransition();

  const handleSuccess = () => { setDialogOpen(false); router.refresh(); };
  const handleDelete = (id: string) => {
    startTransition(async () => { await deleteStorage(id); setDeleteId(null); router.refresh(); });
  };

  const columns: ColumnDef<StorageWithPart>[] = [
    { id: 'name', accessorFn: (r) => r.pcPart.name, header: 'Name', enableSorting: true },
    { id: 'manufacturer', accessorFn: (r) => r.pcPart.manufacturer ?? '', header: 'Manufacturer' },
    { accessorKey: 'storageType', header: 'Type', enableSorting: true },
    { accessorKey: 'capacityGb', header: 'Capacity (GB)', enableSorting: true },
    { accessorKey: 'interface', header: 'Interface', enableSorting: true },
    { id: 'streetPrice', accessorFn: (r) => r.pcPart.streetPriceCents, header: 'Street Price',
      cell: ({ getValue }) => formatUsd(getValue<number | null>()), enableSorting: true },
    { id: 'asin', accessorFn: (r) => getAmazonAsin(r.pcPart.listings), header: 'ASIN', enableSorting: true },
    { id: 'isActive', accessorFn: (r) => r.pcPart.isActive, header: 'Active',
      cell: ({ getValue }) => <Badge variant={getValue<boolean>() ? 'default' : 'secondary'}>{getValue<boolean>() ? 'Active' : 'Inactive'}</Badge> },
    { id: 'actions', header: '', cell: ({ row }) => (
      <div className="flex items-center gap-1">
        <Button variant="ghost" size="sm" onClick={() => { setSelected(row.original); setDialogOpen(true); }}><Pencil className="h-3.5 w-3.5" /></Button>
        <Button variant="ghost" size="sm" className="text-destructive hover:text-destructive" onClick={() => setDeleteId(row.original.id)}><Trash2 className="h-3.5 w-3.5" /></Button>
      </div>
    )},
  ];

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div><h1 className="text-2xl font-bold">Storage</h1><p className="text-muted-foreground text-sm mt-1">{data.length} total</p></div>
        <Button onClick={() => { setSelected(null); setDialogOpen(true); }}>New Storage</Button>
      </div>
      <DataTable columns={columns} data={data} filterPlaceholder="Filter storage..." />
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader><DialogTitle>{selected ? 'Edit Storage' : 'New Storage'}</DialogTitle></DialogHeader>
          <StorageForm item={selected} onSuccess={handleSuccess} />
        </DialogContent>
      </Dialog>
      <AlertDialog open={!!deleteId} onOpenChange={(open) => !open && setDeleteId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader><AlertDialogTitle>Delete Storage?</AlertDialogTitle><AlertDialogDescription>This action cannot be undone.</AlertDialogDescription></AlertDialogHeader>
          <AlertDialogFooter><AlertDialogCancel>Cancel</AlertDialogCancel><AlertDialogAction onClick={() => deleteId && handleDelete(deleteId)}>Delete</AlertDialogAction></AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
