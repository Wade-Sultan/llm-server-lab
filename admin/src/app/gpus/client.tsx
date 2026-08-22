'use client';

import { useState } from 'react';
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
import { Checkbox } from '@/components/ui/checkbox';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ListingsDialog } from '@/components/listings-dialog';
import { createGpu, updateGpu, deleteGpu, type GpuFormData } from './actions';

type GpuWithPart = Gpu & {
  pcPart: PcPart & { listings: (Listing & { amazonListing: AmazonListing | null })[] };
  chipset: { id: string; name: string };
};
type ChipsetOption = { id: string; name: string };

const schema = z.object({
  name: z.string().min(1, 'Name is required'),
  manufacturer: z.string(),
  modelNumber: z.string(),
  yearReleased: z.coerce.number().int().nullable(),
  isActive: z.boolean(),
  gpuChipsetId: z.string().min(1, 'Chipset is required'),
  brand: z.string(),
  lengthMm: z.coerce.number().int().nullable(),
  widthSlots: z.coerce.number().nullable(),
  pciePowerPins: z.string(),
  displayOutputs: z.string(),
  hdmiVersion: z.string(),
  dpVersion: z.string(),
});

function GpuForm({ item, chipsets, onSuccess }: { item: GpuWithPart | null; chipsets: ChipsetOption[]; onSuccess: () => void }) {
  const form = useForm<GpuFormData>({
    resolver: zodResolver(schema),
    defaultValues: item ? {
      name: item.pcPart.name,
      manufacturer: item.pcPart.manufacturer ?? '',
      modelNumber: item.pcPart.modelNumber ?? '',
      yearReleased: item.pcPart.yearReleased,
      isActive: item.pcPart.isActive,
      gpuChipsetId: item.gpuChipsetId,
      brand: item.brand ?? '',
      lengthMm: item.lengthMm,
      widthSlots: item.widthSlots,
      pciePowerPins: item.pciePowerPins ?? '',
      displayOutputs: item.displayOutputs ?? '',
      hdmiVersion: item.hdmiVersion ?? '',
      dpVersion: item.dpVersion ?? '',
    } : {
      name: '', manufacturer: '', modelNumber: '', yearReleased: null, isActive: true,
      gpuChipsetId: '', brand: '', lengthMm: null, widthSlots: null, pciePowerPins: '',
      displayOutputs: '', hdmiVersion: '', dpVersion: '',
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
        <FormField control={form.control} name="gpuChipsetId"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Chipset * (intrinsic spec — VRAM, cores, TDP)</FormLabel>
              <Select value={field.value} onValueChange={field.onChange}>
                <FormControl><SelectTrigger><SelectValue placeholder="Select a chipset" /></SelectTrigger></FormControl>
                <SelectContent>
                  {chipsets.map((c) => <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>)}
                </SelectContent>
              </Select>
              <FormMessage />
            </FormItem>
          )}
        />
        <div className="grid grid-cols-2 gap-4">
          {([
            ['name', 'Product Name *'], ['manufacturer', 'Manufacturer (board partner)'], ['modelNumber', 'Model Number'],
            ['brand', 'Brand (nvidia/amd/intel)'], ['pciePowerPins', 'PCIe Power Pins'],
            ['displayOutputs', 'Display Outputs'], ['hdmiVersion', 'HDMI Version'], ['dpVersion', 'DP Version'],
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
            ['yearReleased', 'Year'], ['lengthMm', 'Length (mm) *'], ['widthSlots', 'Width (slots)'],
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
        </div>
        <FormField control={form.control} name="isActive"
          render={({ field }) => (
            <FormItem className="flex items-center gap-2 space-y-0">
              <FormControl><Checkbox checked={field.value} onCheckedChange={field.onChange} /></FormControl>
              <FormLabel>Active</FormLabel>
            </FormItem>
          )}
        />
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

export function GpuTable({ data, chipsets }: { data: GpuWithPart[]; chipsets: ChipsetOption[] }) {
  const router = useRouter();
  const [selected, setSelected] = useState<GpuWithPart | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deleteId, setDeleteId] = useState<string | null>(null);

  const handleSuccess = () => { setDialogOpen(false); router.refresh(); };
  const handleDelete = async (id: string) => { await deleteGpu(id); setDeleteId(null); router.refresh(); };

  const columns: ColumnDef<GpuWithPart>[] = [
    { id: 'name', accessorFn: (r) => r.pcPart.name, header: 'Name', enableSorting: true },
    { id: 'chipset', accessorFn: (r) => r.chipset.name, header: 'Chipset', enableSorting: true },
    { id: 'manufacturer', accessorFn: (r) => r.pcPart.manufacturer ?? '', header: 'Manufacturer' },
    { accessorKey: 'lengthMm', header: 'Length (mm)', enableSorting: true },
    {
      id: 'listings', header: 'Listings',
      cell: ({ row }) => (
        <ListingsDialog
          partId={row.original.pcPart.id}
          partName={row.original.pcPart.name}
          group={{ kind: 'gpuChipset', id: row.original.chipset.id, name: row.original.chipset.name }}
          listings={row.original.pcPart.listings}
        />
      ),
    },
    {
      id: 'isActive', accessorFn: (r) => r.pcPart.isActive, header: 'Active',
      cell: ({ getValue }) => (
        <Badge variant={getValue<boolean>() ? 'default' : 'secondary'}>{getValue<boolean>() ? 'Active' : 'Inactive'}</Badge>
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
          <p className="text-muted-foreground text-sm mt-1">{data.length} boards</p>
        </div>
        <Button onClick={() => { setSelected(null); setDialogOpen(true); }} disabled={chipsets.length === 0}>New GPU</Button>
      </div>
      {chipsets.length === 0 && (
        <p className="text-sm text-muted-foreground">Create a GPU Chipset first — every board belongs to one.</p>
      )}
      <DataTable columns={columns} data={data} filterPlaceholder="Filter GPUs..." />
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-3xl">
          <DialogHeader><DialogTitle>{selected ? 'Edit GPU' : 'New GPU'}</DialogTitle></DialogHeader>
          <GpuForm item={selected} chipsets={chipsets} onSuccess={handleSuccess} />
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
