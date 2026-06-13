'use client';

import { useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import type { ColumnDef } from '@tanstack/react-table';
import type { CpuCooler, PcPart } from '@prisma/client';
import { Pencil, Trash2 } from 'lucide-react';
import { DataTable } from '@/components/data-table';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog';
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { Checkbox } from '@/components/ui/checkbox';
import { joinCommaList } from '@/lib/utils';
import { createCpuCooler, updateCpuCooler, deleteCpuCooler, type CpuCoolerFormData } from './actions';

type CoolerWithPart = CpuCooler & { pcPart: PcPart };

const schema = z.object({
  name: z.string().min(1), manufacturer: z.string(), modelNumber: z.string(),
  yearReleased: z.coerce.number().int().nullable(), isActive: z.boolean(),
  supportedSocketsInput: z.string(), coolerType: z.string(),
  maxTdpWatts: z.coerce.number().int().nullable(), heightMm: z.coerce.number().int().nullable(),
  radiatorSizeMm: z.coerce.number().int().nullable(), fanCount: z.coerce.number().int().nullable(),
  fanSizeMm: z.coerce.number().int().nullable(), noiseDba: z.coerce.number().nullable(),
  hasRgb: z.boolean(),
});

function CoolerForm({ item, onSuccess }: { item: CoolerWithPart | null; onSuccess: () => void }) {
  const form = useForm<CpuCoolerFormData>({
    resolver: zodResolver(schema),
    defaultValues: item ? {
      name: item.pcPart.name, manufacturer: item.pcPart.manufacturer ?? '',
      modelNumber: item.pcPart.modelNumber ?? '', yearReleased: item.pcPart.yearReleased,
      isActive: item.pcPart.isActive, supportedSocketsInput: joinCommaList(item.supportedSockets),
      coolerType: item.coolerType ?? '', maxTdpWatts: item.maxTdpWatts, heightMm: item.heightMm,
      radiatorSizeMm: item.radiatorSizeMm, fanCount: item.fanCount, fanSizeMm: item.fanSizeMm,
      noiseDba: item.noiseDba, hasRgb: item.hasRgb,
    } : {
      name: '', manufacturer: '', modelNumber: '', yearReleased: null, isActive: true,
      supportedSocketsInput: '', coolerType: '', maxTdpWatts: null, heightMm: null,
      radiatorSizeMm: null, fanCount: null, fanSizeMm: null, noiseDba: null, hasRgb: false,
    },
  });

  const [error, setError] = useState<string | null>(null);
  const numChange = (onChange: (v: number | null) => void) => (e: React.ChangeEvent<HTMLInputElement>) =>
    onChange(e.target.value === '' ? null : Number(e.target.value));

  async function onSubmit(data: CpuCoolerFormData) {
    setError(null);
    try {
      if (item) { await updateCpuCooler(item.id, data); } else { await createCpuCooler(data); }
      onSuccess();
    } catch (e) { setError(e instanceof Error ? e.message : 'An error occurred'); }
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          {([ ['name','Name *'], ['manufacturer','Manufacturer'], ['modelNumber','Model Number'], ['coolerType','Cooler Type (Air/AIO)'] ] as [keyof CpuCoolerFormData, string][]).map(([name, label]) => (
            <FormField key={name} control={form.control} name={name}
              render={({ field }) => (
                <FormItem><FormLabel>{label}</FormLabel>
                  <FormControl><Input {...field} value={field.value as string ?? ''} /></FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          ))}
          {([ ['yearReleased','Year'], ['maxTdpWatts','Max TDP (W)'], ['heightMm','Height (mm)'],
              ['radiatorSizeMm','Radiator Size (mm)'], ['fanCount','Fan Count'],
              ['fanSizeMm','Fan Size (mm)'], ['noiseDba','Noise (dBA)'],
          ] as [keyof CpuCoolerFormData, string][]).map(([name, label]) => (
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
        </div>
        <FormField control={form.control} name="supportedSocketsInput"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Supported Sockets (comma-separated, e.g. LGA1700, AM5)</FormLabel>
              <FormControl><Input {...field} /></FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <div className="flex gap-6">
          {([ ['hasRgb','RGB'], ['isActive','Active'] ] as [keyof CpuCoolerFormData, string][]).map(([name, label]) => (
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

export function CpuCoolerTable({ data }: { data: CoolerWithPart[] }) {
  const router = useRouter();
  const [selected, setSelected] = useState<CoolerWithPart | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const [, startTransition] = useTransition();

  const handleSuccess = () => { setDialogOpen(false); router.refresh(); };
  const handleDelete = (id: string) => {
    startTransition(async () => { await deleteCpuCooler(id); setDeleteId(null); router.refresh(); });
  };

  const columns: ColumnDef<CoolerWithPart>[] = [
    { id: 'name', accessorFn: (r) => r.pcPart.name, header: 'Name', enableSorting: true },
    { id: 'manufacturer', accessorFn: (r) => r.pcPart.manufacturer ?? '', header: 'Manufacturer' },
    { accessorKey: 'coolerType', header: 'Type', enableSorting: true },
    { accessorKey: 'maxTdpWatts', header: 'Max TDP (W)', enableSorting: true },
    { accessorKey: 'heightMm', header: 'Height (mm)', enableSorting: true },
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
        <div><h1 className="text-2xl font-bold">CPU Coolers</h1><p className="text-muted-foreground text-sm mt-1">{data.length} total</p></div>
        <Button onClick={() => { setSelected(null); setDialogOpen(true); }}>New CPU Cooler</Button>
      </div>
      <DataTable columns={columns} data={data} filterPlaceholder="Filter CPU coolers..." />
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader><DialogTitle>{selected ? 'Edit CPU Cooler' : 'New CPU Cooler'}</DialogTitle></DialogHeader>
          <CoolerForm item={selected} onSuccess={handleSuccess} />
        </DialogContent>
      </Dialog>
      <AlertDialog open={!!deleteId} onOpenChange={(open) => !open && setDeleteId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader><AlertDialogTitle>Delete CPU Cooler?</AlertDialogTitle><AlertDialogDescription>This action cannot be undone.</AlertDialogDescription></AlertDialogHeader>
          <AlertDialogFooter><AlertDialogCancel>Cancel</AlertDialogCancel><AlertDialogAction onClick={() => deleteId && handleDelete(deleteId)}>Delete</AlertDialogAction></AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
