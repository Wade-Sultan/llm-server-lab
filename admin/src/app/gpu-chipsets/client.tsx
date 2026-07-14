'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import type { ColumnDef } from '@tanstack/react-table';
import type { GpuChipset } from '@prisma/client';
import { Pencil, Trash2 } from 'lucide-react';
import { DataTable } from '@/components/data-table';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Checkbox } from '@/components/ui/checkbox';
import { joinCommaList, centsToUsd, formatUsd } from '@/lib/utils';
import { createGpuChipset, updateGpuChipset, deleteGpuChipset, type GpuChipsetFormData } from './actions';

const schema = z.object({
  name: z.string().min(1, 'Name is required'),
  streetPriceUsd: z.coerce.number().nullable(),
  vramGb: z.coerce.number().int().nullable(),
  vramType: z.string(),
  tdpWatts: z.coerce.number().int().nullable(),
  recommendedPsuWatts: z.coerce.number().int().nullable(),
  pcieGeneration: z.coerce.number().int().nullable(),
  baseClockMhz: z.coerce.number().int().nullable(),
  boostClockMhz: z.coerce.number().int().nullable(),
  hasRayTracing: z.boolean(),
  cudaCores: z.coerce.number().int().nullable(),
  tensorCores: z.coerce.number().int().nullable(),
  streamProcessors: z.coerce.number().int().nullable(),
  matrixCores: z.coerce.number().int().nullable(),
  supportedFeaturesInput: z.string(),
  benchmarkScoresInput: z.string(),
});

function ChipsetForm({ item, onSuccess }: { item: GpuChipset | null; onSuccess: () => void }) {
  const form = useForm<GpuChipsetFormData>({
    resolver: zodResolver(schema),
    defaultValues: item ? {
      name: item.name,
      streetPriceUsd: centsToUsd(item.streetPriceCents),
      vramGb: item.vramGb,
      vramType: item.vramType ?? '',
      tdpWatts: item.tdpWatts,
      recommendedPsuWatts: item.recommendedPsuWatts,
      pcieGeneration: item.pcieGeneration,
      baseClockMhz: item.baseClockMhz,
      boostClockMhz: item.boostClockMhz,
      hasRayTracing: item.hasRayTracing,
      cudaCores: item.cudaCores,
      tensorCores: item.tensorCores,
      streamProcessors: item.streamProcessors,
      matrixCores: item.matrixCores,
      supportedFeaturesInput: joinCommaList(item.supportedFeatures),
      benchmarkScoresInput: item.benchmarkScores ? JSON.stringify(item.benchmarkScores, null, 2) : '',
    } : {
      name: '', streetPriceUsd: null, vramGb: null, vramType: '', tdpWatts: null, recommendedPsuWatts: null,
      pcieGeneration: null, baseClockMhz: null, boostClockMhz: null, hasRayTracing: false,
      cudaCores: null, tensorCores: null, streamProcessors: null, matrixCores: null,
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

  async function onSubmit(data: GpuChipsetFormData) {
    setError(null);
    try {
      if (item) { await updateGpuChipset(item.id, data); } else { await createGpuChipset(data); }
      onSuccess();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'An error occurred');
    }
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          {([['name', 'Name * (e.g. "RTX 5080")'], ['vramType', 'VRAM Type']] as [keyof GpuChipsetFormData, string][]).map(([name, label]) => (
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
            ['vramGb', 'VRAM (GB) *'], ['tdpWatts', 'TDP (W) *'], ['recommendedPsuWatts', 'Rec. PSU (W)'],
            ['pcieGeneration', 'PCIe Gen'], ['baseClockMhz', 'Base Clock (MHz)'], ['boostClockMhz', 'Boost Clock (MHz)'],
            ['cudaCores', 'CUDA Cores'], ['tensorCores', 'Tensor Cores'], ['streamProcessors', 'Stream Procs'],
            ['matrixCores', 'Matrix Cores'],
          ] as [keyof GpuChipsetFormData, string][]).map(([name, label]) => (
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
        <FormField control={form.control} name="hasRayTracing"
          render={({ field }) => (
            <FormItem className="flex items-center gap-2 space-y-0">
              <FormControl><Checkbox checked={field.value} onCheckedChange={field.onChange} /></FormControl>
              <FormLabel>Has Ray Tracing</FormLabel>
            </FormItem>
          )}
        />
        {error && <p className="text-sm text-destructive">{error}</p>}
        <div className="flex justify-end pt-2">
          <Button type="submit" disabled={form.formState.isSubmitting}>
            {form.formState.isSubmitting ? 'Saving...' : item ? 'Update Chipset' : 'Create Chipset'}
          </Button>
        </div>
      </form>
    </Form>
  );
}

export function GpuChipsetTable({ data }: { data: GpuChipset[] }) {
  const router = useRouter();
  const [selected, setSelected] = useState<GpuChipset | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deleteId, setDeleteId] = useState<string | null>(null);

  const handleSuccess = () => { setDialogOpen(false); router.refresh(); };
  const handleDelete = async (id: string) => { await deleteGpuChipset(id); setDeleteId(null); router.refresh(); };

  const columns: ColumnDef<GpuChipset>[] = [
    { accessorKey: 'name', header: 'Name', enableSorting: true },
    { accessorKey: 'vramGb', header: 'VRAM (GB)', enableSorting: true },
    { accessorKey: 'tdpWatts', header: 'TDP (W)', enableSorting: true },
    { accessorKey: 'recommendedPsuWatts', header: 'Rec. PSU (W)' },
    {
      id: 'streetPrice', accessorFn: (r) => r.streetPriceCents, header: 'Street Price',
      cell: ({ getValue }) => formatUsd(getValue<number | null>()), enableSorting: true,
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
          <h1 className="text-2xl font-bold">GPU Chipsets</h1>
          <p className="text-muted-foreground text-sm mt-1">{data.length} total · shared spec for GPU boards</p>
        </div>
        <Button onClick={() => { setSelected(null); setDialogOpen(true); }}>New Chipset</Button>
      </div>
      <DataTable columns={columns} data={data} filterPlaceholder="Filter chipsets..." />
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-3xl">
          <DialogHeader><DialogTitle>{selected ? 'Edit Chipset' : 'New Chipset'}</DialogTitle></DialogHeader>
          <ChipsetForm item={selected} onSuccess={handleSuccess} />
        </DialogContent>
      </Dialog>
      <AlertDialog open={!!deleteId} onOpenChange={(open) => !open && setDeleteId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Chipset?</AlertDialogTitle>
            <AlertDialogDescription>
              This cannot be undone. Deleting fails if any GPU board still references it.
            </AlertDialogDescription>
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
