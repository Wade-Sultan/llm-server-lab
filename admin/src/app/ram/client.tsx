'use client';

import { useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import type { ColumnDef } from '@tanstack/react-table';
import type { Ram, PcPart, Listing, AmazonListing } from '@prisma/client';
import { Pencil, Trash2 } from 'lucide-react';
import { DataTable } from '@/components/data-table';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog';
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { Checkbox } from '@/components/ui/checkbox';
import { ListingsDialog } from '@/components/listings-dialog';
import { centsToUsd, formatUsd } from '@/lib/utils';
import { createRam, updateRam, deleteRam, type RamFormData } from './actions';

type RamWithPart = Ram & { pcPart: PcPart & { listings: (Listing & { amazonListing: AmazonListing | null })[] } };

const schema = z.object({
  name: z.string().min(1), manufacturer: z.string(), modelNumber: z.string(),
  yearReleased: z.coerce.number().int().nullable(), isActive: z.boolean(),
  streetPriceUsd: z.coerce.number().nullable(),
  ddrGeneration: z.string(), speedMhz: z.coerce.number().int().nullable(),
  modules: z.coerce.number().int().nullable(), capacityGb: z.coerce.number().int().nullable(),
  heightMm: z.coerce.number().int().nullable(), moduleCapacityGb: z.coerce.number().int().nullable(),
  casLatency: z.coerce.number().int().nullable(), voltage: z.coerce.number().nullable(),
  hasRgb: z.boolean(), isEcc: z.boolean(),
});

function RamForm({ item, onSuccess }: { item: RamWithPart | null; onSuccess: () => void }) {
  const form = useForm<RamFormData>({
    resolver: zodResolver(schema),
    defaultValues: item ? {
      name: item.pcPart.name, manufacturer: item.pcPart.manufacturer ?? '',
      modelNumber: item.pcPart.modelNumber ?? '', yearReleased: item.pcPart.yearReleased,
      isActive: item.pcPart.isActive, streetPriceUsd: centsToUsd(item.pcPart.streetPriceCents),
      ddrGeneration: item.ddrGeneration ?? '',
      speedMhz: item.speedMhz, modules: item.modules, capacityGb: item.capacityGb,
      heightMm: item.heightMm, moduleCapacityGb: item.moduleCapacityGb,
      casLatency: item.casLatency, voltage: item.voltage, hasRgb: item.hasRgb, isEcc: item.isEcc,
    } : {
      name: '', manufacturer: '', modelNumber: '', yearReleased: null, isActive: true,
      streetPriceUsd: null,
      ddrGeneration: '', speedMhz: null, modules: null, capacityGb: null, heightMm: null,
      moduleCapacityGb: null, casLatency: null, voltage: null, hasRgb: false, isEcc: false,
    },
  });

  const [error, setError] = useState<string | null>(null);
  const numChange = (onChange: (v: number | null) => void) => (e: React.ChangeEvent<HTMLInputElement>) =>
    onChange(e.target.value === '' ? null : Number(e.target.value));

  async function onSubmit(data: RamFormData) {
    setError(null);
    try {
      if (item) { await updateRam(item.id, data); } else { await createRam(data); }
      onSuccess();
    } catch (e) { setError(e instanceof Error ? e.message : 'An error occurred'); }
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          {([ ['name','Name *'], ['manufacturer','Manufacturer'], ['modelNumber','Model Number'], ['ddrGeneration','DDR Generation'] ] as [keyof RamFormData, string][]).map(([name, label]) => (
            <FormField key={name} control={form.control} name={name}
              render={({ field }) => (
                <FormItem><FormLabel>{label}</FormLabel>
                  <FormControl><Input {...field} value={field.value as string ?? ''} /></FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          ))}
          {([ ['yearReleased','Year'], ['speedMhz','Speed (MHz)'], ['modules','Modules'], ['capacityGb','Capacity (GB)'],
              ['heightMm','Height (mm)'], ['moduleCapacityGb','Module Cap (GB)'], ['casLatency','CAS Latency'], ['voltage','Voltage (V)'],
          ] as [keyof RamFormData, string][]).map(([name, label]) => (
            <FormField key={name} control={form.control} name={name}
              render={({ field }) => (
                <FormItem><FormLabel>{label}</FormLabel>
                  <FormControl>
                    <Input type="number" step="any" value={(field.value as number | null) ?? ''} onChange={numChange(field.onChange)} />
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
        </div>
        <div className="flex gap-6">
          {([ ['hasRgb','RGB'], ['isEcc','ECC'], ['isActive','Active'] ] as [keyof RamFormData, string][]).map(([name, label]) => (
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

export function RamTable({ data }: { data: RamWithPart[] }) {
  const router = useRouter();
  const [selected, setSelected] = useState<RamWithPart | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const [, startTransition] = useTransition();

  const handleSuccess = () => { setDialogOpen(false); router.refresh(); };
  const handleDelete = (id: string) => {
    startTransition(async () => { await deleteRam(id); setDeleteId(null); router.refresh(); });
  };

  const columns: ColumnDef<RamWithPart>[] = [
    { id: 'name', accessorFn: (r) => r.pcPart.name, header: 'Name', enableSorting: true },
    { id: 'manufacturer', accessorFn: (r) => r.pcPart.manufacturer ?? '', header: 'Manufacturer' },
    { accessorKey: 'ddrGeneration', header: 'DDR', enableSorting: true },
    { accessorKey: 'speedMhz', header: 'Speed (MHz)', enableSorting: true },
    { accessorKey: 'capacityGb', header: 'Capacity (GB)', enableSorting: true },
    { id: 'streetPrice', accessorFn: (r) => r.pcPart.streetPriceCents, header: 'Street Price',
      cell: ({ getValue }) => formatUsd(getValue<number | null>()), enableSorting: true },
    {
      id: 'listings', header: 'Listings',
      cell: ({ row }) => (
        <ListingsDialog
          partId={row.original.pcPart.id}
          partName={row.original.pcPart.name}
          listings={row.original.pcPart.listings}
        />
      ),
    },
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
        <div><h1 className="text-2xl font-bold">RAM</h1><p className="text-muted-foreground text-sm mt-1">{data.length} total</p></div>
        <Button onClick={() => { setSelected(null); setDialogOpen(true); }}>New RAM</Button>
      </div>
      <DataTable columns={columns} data={data} filterPlaceholder="Filter RAM..." />
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader><DialogTitle>{selected ? 'Edit RAM' : 'New RAM'}</DialogTitle></DialogHeader>
          <RamForm item={selected} onSuccess={handleSuccess} />
        </DialogContent>
      </Dialog>
      <AlertDialog open={!!deleteId} onOpenChange={(open) => !open && setDeleteId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader><AlertDialogTitle>Delete RAM?</AlertDialogTitle><AlertDialogDescription>This action cannot be undone.</AlertDialogDescription></AlertDialogHeader>
          <AlertDialogFooter><AlertDialogCancel>Cancel</AlertDialogCancel><AlertDialogAction onClick={() => deleteId && handleDelete(deleteId)}>Delete</AlertDialogAction></AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
