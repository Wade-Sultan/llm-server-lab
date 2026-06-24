'use client';

import { useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import type { ColumnDef } from '@tanstack/react-table';
import type { Gpu, PcPart, Listing, AmazonListing } from '@prisma/client';
import { Pencil, Trash2 } from 'lucide-react';
import { DataTable } from '@/components/data-table';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Checkbox } from '@/components/ui/checkbox';
import { joinCommaList, centsToUsd, formatUsd, asinSchema, getAmazonAsin } from '@/lib/utils';
import { createGpu, updateGpu, deleteGpu, type GpuFormData } from './actions';

type GpuWithPart = Gpu & { pcPart: PcPart & { listings: (Listing & { amazonListing: AmazonListing | null })[] } };

const schema = z.object({
  name: z.string().min(1, 'Name is required'),
  manufacturer: z.string(),
  modelNumber: z.string(),
  yearReleased: z.coerce.number().int().nullable(),
  isActive: z.boolean(),
  streetPriceUsd: z.coerce.number().nullable(),
  asin: asinSchema,
  brand: z.string(),
  chipset: z.string(),
  vramGb: z.coerce.number().int().nullable(),
  tdpWatts: z.coerce.number().int().nullable(),
  lengthMm: z.coerce.number().int().nullable(),
  pciePowerPins: z.string(),
  recommendedPsuWatts: z.coerce.number().int().nullable(),
  vramType: z.string(),
  widthSlots: z.coerce.number().nullable(),
  pcieGeneration: z.coerce.number().int().nullable(),
  baseClockMhz: z.coerce.number().int().nullable(),
  boostClockMhz: z.coerce.number().int().nullable(),
  hasRayTracing: z.boolean(),
  cudaCores: z.coerce.number().int().nullable(),
  tensorCores: z.coerce.number().int().nullable(),
  streamProcessors: z.coerce.number().int().nullable(),
  matrixCores: z.coerce.number().int().nullable(),
  displayOutputs: z.string(),
  hdmiVersion: z.string(),
  dpVersion: z.string(),
  supportedFeaturesInput: z.string(),
  benchmarkScoresInput: z.string(),
});

function GpuForm({ item, onSuccess }: { item: GpuWithPart | null; onSuccess: () => void }) {
  const form = useForm<GpuFormData>({
    resolver: zodResolver(schema),
    defaultValues: item ? {
      name: item.pcPart.name,
      manufacturer: item.pcPart.manufacturer ?? '',
      modelNumber: item.pcPart.modelNumber ?? '',
      yearReleased: item.pcPart.yearReleased,
      isActive: item.pcPart.isActive,
      streetPriceUsd: centsToUsd(item.pcPart.streetPriceCents),
      asin: getAmazonAsin(item.pcPart.listings),
      brand: item.brand ?? '',
      chipset: item.chipset ?? '',
      vramGb: item.vramGb,
      tdpWatts: item.tdpWatts,
      lengthMm: item.lengthMm,
      pciePowerPins: item.pciePowerPins ?? '',
      recommendedPsuWatts: item.recommendedPsuWatts,
      vramType: item.vramType ?? '',
      widthSlots: item.widthSlots,
      pcieGeneration: item.pcieGeneration,
      baseClockMhz: item.baseClockMhz,
      boostClockMhz: item.boostClockMhz,
      hasRayTracing: item.hasRayTracing,
      cudaCores: item.cudaCores,
      tensorCores: item.tensorCores,
      streamProcessors: item.streamProcessors,
      matrixCores: item.matrixCores,
      displayOutputs: item.displayOutputs ?? '',
      hdmiVersion: item.hdmiVersion ?? '',
      dpVersion: item.dpVersion ?? '',
      supportedFeaturesInput: joinCommaList(item.supportedFeatures),
      benchmarkScoresInput: item.benchmarkScores ? JSON.stringify(item.benchmarkScores, null, 2) : '',
    } : {
      name: '', manufacturer: '', modelNumber: '', yearReleased: null, isActive: true,
      streetPriceUsd: null,
      asin: '',
      brand: '', chipset: '', vramGb: null, tdpWatts: null, lengthMm: null,
      pciePowerPins: '', recommendedPsuWatts: null, vramType: '', widthSlots: null,
      pcieGeneration: null, baseClockMhz: null, boostClockMhz: null, hasRayTracing: false,
      cudaCores: null, tensorCores: null, streamProcessors: null, matrixCores: null,
      displayOutputs: '', hdmiVersion: '', dpVersion: '',
      supportedFeaturesInput: '', benchmarkScoresInput: '',
    },
  });

  const [error, setError] = useState<string | null>(null);

  const numField = (field: { value: number | null; onChange: (v: number | null) => void }) => ({
    ...field,
    type: 'number' as const,
    value: field.value ?? '',
    onChange: (e: React.ChangeEvent<HTMLInputElement>) =>
      field.onChange(e.target.value === '' ? null : Number(e.target.value)),
  });

  async function onSubmit(data: GpuFormData) {
    setError(null);
    try {
      if (item) { await updateGpu(item.id, data); } else { await createGpu(data); }
      onSuccess();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'An error occurred');
    }
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          {([
            ['name', 'Name *'], ['manufacturer', 'Manufacturer'], ['modelNumber', 'Model Number'],
            ['brand', 'Brand'], ['chipset', 'Chipset'], ['vramType', 'VRAM Type'],
            ['pciePowerPins', 'PCIe Power Pins'], ['displayOutputs', 'Display Outputs'],
            ['hdmiVersion', 'HDMI Version'], ['dpVersion', 'DP Version'],
          ] as [keyof GpuFormData, string][]).map(([name, label]) => (
            <FormField key={name} control={form.control} name={name}
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{label}</FormLabel>
                  <FormControl><Input {...field} value={field.value as string ?? ''} /></FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          ))}
          {([
            ['yearReleased', 'Year'], ['vramGb', 'VRAM (GB)'], ['tdpWatts', 'TDP (W)'],
            ['lengthMm', 'Length (mm)'], ['recommendedPsuWatts', 'Rec. PSU (W)'],
            ['pcieGeneration', 'PCIe Gen'], ['baseClockMhz', 'Base Clock (MHz)'],
            ['boostClockMhz', 'Boost Clock (MHz)'], ['cudaCores', 'CUDA Cores'],
            ['tensorCores', 'Tensor Cores'], ['streamProcessors', 'Stream Procs'],
            ['matrixCores', 'Matrix Cores'], ['widthSlots', 'Width (slots)'],
          ] as [keyof GpuFormData, string][]).map(([name, label]) => (
            <FormField key={name} control={form.control} name={name}
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{label}</FormLabel>
                  <FormControl>
                    <Input {...numField(field as { value: number | null; onChange: (v: number | null) => void })} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          ))}
          <FormField control={form.control} name="streetPriceUsd"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Street Price (USD)</FormLabel>
                <FormControl>
                  <Input {...numField(field as { value: number | null; onChange: (v: number | null) => void })} step="0.01" />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField control={form.control} name="asin"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Amazon ASIN</FormLabel>
                <FormControl><Input {...field} placeholder="B0XXXXXXXX" maxLength={10} /></FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
        </div>
        <FormField control={form.control} name="supportedFeaturesInput"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Supported Features (comma-separated)</FormLabel>
              <FormControl><Input {...field} /></FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField control={form.control} name="benchmarkScoresInput"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Benchmark Scores (JSON)</FormLabel>
              <FormControl><Textarea rows={4} {...field} /></FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <div className="flex gap-6">
          {([ ['hasRayTracing', 'Has Ray Tracing'], ['isActive', 'Active'] ] as [keyof GpuFormData, string][]).map(([name, label]) => (
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
            {form.formState.isSubmitting ? 'Saving...' : item ? 'Update GPU' : 'Create GPU'}
          </Button>
        </div>
      </form>
    </Form>
  );
}

export function GpuTable({ data }: { data: GpuWithPart[] }) {
  const router = useRouter();
  const [selected, setSelected] = useState<GpuWithPart | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const [, startTransition] = useTransition();

  const handleSuccess = () => { setDialogOpen(false); router.refresh(); };
  const handleDelete = (id: string) => {
    startTransition(async () => { await deleteGpu(id); setDeleteId(null); router.refresh(); });
  };

  const columns: ColumnDef<GpuWithPart>[] = [
    { id: 'name', accessorFn: (r) => r.pcPart.name, header: 'Name', enableSorting: true },
    { id: 'manufacturer', accessorFn: (r) => r.pcPart.manufacturer ?? '', header: 'Manufacturer' },
    { accessorKey: 'chipset', header: 'Chipset', enableSorting: true },
    { accessorKey: 'vramGb', header: 'VRAM (GB)', enableSorting: true },
    { accessorKey: 'tdpWatts', header: 'TDP (W)', enableSorting: true },
    {
      id: 'streetPrice', accessorFn: (r) => r.pcPart.streetPriceCents, header: 'Street Price',
      cell: ({ getValue }) => formatUsd(getValue<number | null>()), enableSorting: true,
    },
    {
      id: 'asin', accessorFn: (r) => getAmazonAsin(r.pcPart.listings), header: 'ASIN', enableSorting: true,
    },
    {
      id: 'isActive', accessorFn: (r) => r.pcPart.isActive, header: 'Active',
      cell: ({ getValue }) => (
        <Badge variant={getValue<boolean>() ? 'default' : 'secondary'}>
          {getValue<boolean>() ? 'Active' : 'Inactive'}
        </Badge>
      ),
    },
    {
      id: 'actions', header: '',
      cell: ({ row }) => (
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="sm" onClick={() => { setSelected(row.original); setDialogOpen(true); }}>
            <Pencil className="h-3.5 w-3.5" />
          </Button>
          <Button variant="ghost" size="sm" className="text-destructive hover:text-destructive" onClick={() => setDeleteId(row.original.id)}>
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      ),
    },
  ];

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">GPUs</h1>
          <p className="text-muted-foreground text-sm mt-1">{data.length} total</p>
        </div>
        <Button onClick={() => { setSelected(null); setDialogOpen(true); }}>New GPU</Button>
      </div>
      <DataTable columns={columns} data={data} filterPlaceholder="Filter GPUs..." />
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-3xl">
          <DialogHeader><DialogTitle>{selected ? 'Edit GPU' : 'New GPU'}</DialogTitle></DialogHeader>
          <GpuForm item={selected} onSuccess={handleSuccess} />
        </DialogContent>
      </Dialog>
      <AlertDialog open={!!deleteId} onOpenChange={(open) => !open && setDeleteId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete GPU?</AlertDialogTitle>
            <AlertDialogDescription>This action cannot be undone.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => deleteId && handleDelete(deleteId)}>Delete</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
