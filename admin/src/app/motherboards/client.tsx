'use client';

import { useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import type { ColumnDef } from '@tanstack/react-table';
import type { Motherboard, PcPart, Listing, AmazonListing } from '@prisma/client';
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
import { createMotherboard, updateMotherboard, deleteMotherboard, type MotherboardFormData } from './actions';

type MoboWithPart = Motherboard & { pcPart: PcPart & { listings: (Listing & { amazonListing: AmazonListing | null })[] } };

const schema = z.object({
  name: z.string().min(1), manufacturer: z.string(), modelNumber: z.string(),
  yearReleased: z.coerce.number().int().nullable(), isActive: z.boolean(),
  streetPriceUsd: z.coerce.number().nullable(),
  asin: asinSchema,
  socket: z.string(), formFactor: z.string(), ddrGeneration: z.string(),
  memorySlots: z.coerce.number().int().nullable(), hasWifi: z.boolean(),
  m2Slots: z.coerce.number().int().nullable(), m2PcieGen: z.coerce.number().int().nullable(),
  chipset: z.string(), maxMemoryGb: z.coerce.number().int().nullable(),
  sataPorts: z.coerce.number().int().nullable(), pcieX16Slots: z.coerce.number().int().nullable(),
  pcieGeneration: z.coerce.number().int().nullable(), hasBluetooth: z.boolean(),
  usbTypeACount: z.coerce.number().int().nullable(), usbTypeCCount: z.coerce.number().int().nullable(),
  audioCodec: z.string(),
});

function MotherboardForm({ item, onSuccess }: { item: MoboWithPart | null; onSuccess: () => void }) {
  const form = useForm<MotherboardFormData>({
    resolver: zodResolver(schema),
    defaultValues: item ? {
      name: item.pcPart.name, manufacturer: item.pcPart.manufacturer ?? '', modelNumber: item.pcPart.modelNumber ?? '',
      yearReleased: item.pcPart.yearReleased, isActive: item.pcPart.isActive,
      streetPriceUsd: centsToUsd(item.pcPart.streetPriceCents),
      asin: getAmazonAsin(item.pcPart.listings),
      socket: item.socket ?? '', formFactor: item.formFactor ?? '', ddrGeneration: item.ddrGeneration ?? '',
      memorySlots: item.memorySlots, hasWifi: item.hasWifi, m2Slots: item.m2Slots, m2PcieGen: item.m2PcieGen,
      chipset: item.chipset ?? '', maxMemoryGb: item.maxMemoryGb, sataPorts: item.sataPorts,
      pcieX16Slots: item.pcieX16Slots, pcieGeneration: item.pcieGeneration, hasBluetooth: item.hasBluetooth,
      usbTypeACount: item.usbTypeACount, usbTypeCCount: item.usbTypeCCount, audioCodec: item.audioCodec ?? '',
    } : {
      name: '', manufacturer: '', modelNumber: '', yearReleased: null, isActive: true,
      streetPriceUsd: null,
      asin: '',
      socket: '', formFactor: '', ddrGeneration: '', memorySlots: null, hasWifi: false,
      m2Slots: null, m2PcieGen: null, chipset: '', maxMemoryGb: null, sataPorts: null,
      pcieX16Slots: null, pcieGeneration: null, hasBluetooth: false, usbTypeACount: null,
      usbTypeCCount: null, audioCodec: '',
    },
  });

  const [error, setError] = useState<string | null>(null);
  const numChange = (onChange: (v: number | null) => void) => (e: React.ChangeEvent<HTMLInputElement>) =>
    onChange(e.target.value === '' ? null : Number(e.target.value));

  async function onSubmit(data: MotherboardFormData) {
    setError(null);
    try {
      if (item) { await updateMotherboard(item.id, data); } else { await createMotherboard(data); }
      onSuccess();
    } catch (e) { setError(e instanceof Error ? e.message : 'An error occurred'); }
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          {([ ['name', 'Name *'], ['manufacturer', 'Manufacturer'], ['modelNumber', 'Model Number'],
              ['socket', 'Socket'], ['formFactor', 'Form Factor'], ['ddrGeneration', 'DDR Gen'],
              ['chipset', 'Chipset'], ['audioCodec', 'Audio Codec'],
          ] as [keyof MotherboardFormData, string][]).map(([name, label]) => (
            <FormField key={name} control={form.control} name={name}
              render={({ field }) => (
                <FormItem><FormLabel>{label}</FormLabel>
                  <FormControl><Input {...field} value={field.value as string ?? ''} /></FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          ))}
          {([ ['yearReleased','Year'], ['memorySlots','Memory Slots'], ['m2Slots','M.2 Slots'],
              ['m2PcieGen','M.2 PCIe Gen'], ['maxMemoryGb','Max Mem (GB)'], ['sataPorts','SATA Ports'],
              ['pcieX16Slots','PCIe x16 Slots'], ['pcieGeneration','PCIe Gen'],
              ['usbTypeACount','USB-A Count'], ['usbTypeCCount','USB-C Count'],
          ] as [keyof MotherboardFormData, string][]).map(([name, label]) => (
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
          {([ ['hasWifi', 'Wi-Fi'], ['hasBluetooth', 'Bluetooth'], ['isActive', 'Active'] ] as [keyof MotherboardFormData, string][]).map(([name, label]) => (
            <FormField key={name} control={form.control} name={name}
              render={({ field }) => (
                <FormItem className="flex items-center gap-2 space-y-0">
                  <FormControl>
                    <Checkbox checked={field.value as boolean} onCheckedChange={field.onChange} />
                  </FormControl>
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

export function MotherboardTable({ data }: { data: MoboWithPart[] }) {
  const router = useRouter();
  const [selected, setSelected] = useState<MoboWithPart | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const [, startTransition] = useTransition();

  const handleSuccess = () => { setDialogOpen(false); router.refresh(); };
  const handleDelete = (id: string) => {
    startTransition(async () => { await deleteMotherboard(id); setDeleteId(null); router.refresh(); });
  };

  const columns: ColumnDef<MoboWithPart>[] = [
    { id: 'name', accessorFn: (r) => r.pcPart.name, header: 'Name', enableSorting: true },
    { id: 'manufacturer', accessorFn: (r) => r.pcPart.manufacturer ?? '', header: 'Manufacturer' },
    { accessorKey: 'socket', header: 'Socket', enableSorting: true },
    { accessorKey: 'formFactor', header: 'Form Factor', enableSorting: true },
    { accessorKey: 'chipset', header: 'Chipset', enableSorting: true },
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
        <div><h1 className="text-2xl font-bold">Motherboards</h1><p className="text-muted-foreground text-sm mt-1">{data.length} total</p></div>
        <Button onClick={() => { setSelected(null); setDialogOpen(true); }}>New Motherboard</Button>
      </div>
      <DataTable columns={columns} data={data} filterPlaceholder="Filter motherboards..." />
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-3xl">
          <DialogHeader><DialogTitle>{selected ? 'Edit Motherboard' : 'New Motherboard'}</DialogTitle></DialogHeader>
          <MotherboardForm item={selected} onSuccess={handleSuccess} />
        </DialogContent>
      </Dialog>
      <AlertDialog open={!!deleteId} onOpenChange={(open) => !open && setDeleteId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader><AlertDialogTitle>Delete Motherboard?</AlertDialogTitle><AlertDialogDescription>This action cannot be undone.</AlertDialogDescription></AlertDialogHeader>
          <AlertDialogFooter><AlertDialogCancel>Cancel</AlertDialogCancel><AlertDialogAction onClick={() => deleteId && handleDelete(deleteId)}>Delete</AlertDialogAction></AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
