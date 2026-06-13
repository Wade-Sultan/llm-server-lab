'use client';

import { useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import type { ColumnDef } from '@tanstack/react-table';
import type { Psu, PcPart } from '@prisma/client';
import { Pencil, Trash2 } from 'lucide-react';
import { DataTable } from '@/components/data-table';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog';
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { Checkbox } from '@/components/ui/checkbox';
import { createPsu, updatePsu, deletePsu, type PsuFormData } from './actions';

type PsuWithPart = Psu & { pcPart: PcPart };

const schema = z.object({
  name: z.string().min(1), manufacturer: z.string(), modelNumber: z.string(),
  yearReleased: z.coerce.number().int().nullable(), isActive: z.boolean(),
  wattage: z.coerce.number().int().nullable(), formFactor: z.string(),
  efficiencyRating: z.string(), pcie8pinConnectors: z.coerce.number().int().nullable(),
  pcie12pinConnectors: z.coerce.number().int().nullable(), pcie16pinConnectors: z.coerce.number().int().nullable(),
  depthMm: z.coerce.number().int().nullable(), modular: z.string(),
  epsConnectors: z.coerce.number().int().nullable(), fanSizeMm: z.coerce.number().int().nullable(),
  isFanless: z.boolean(),
});

function PsuForm({ item, onSuccess }: { item: PsuWithPart | null; onSuccess: () => void }) {
  const form = useForm<PsuFormData>({
    resolver: zodResolver(schema),
    defaultValues: item ? {
      name: item.pcPart.name, manufacturer: item.pcPart.manufacturer ?? '',
      modelNumber: item.pcPart.modelNumber ?? '', yearReleased: item.pcPart.yearReleased,
      isActive: item.pcPart.isActive, wattage: item.wattage, formFactor: item.formFactor ?? '',
      efficiencyRating: item.efficiencyRating ?? '', pcie8pinConnectors: item.pcie8pinConnectors,
      pcie12pinConnectors: item.pcie12pinConnectors, pcie16pinConnectors: item.pcie16pinConnectors,
      depthMm: item.depthMm, modular: item.modular ?? '', epsConnectors: item.epsConnectors,
      fanSizeMm: item.fanSizeMm, isFanless: item.isFanless,
    } : {
      name: '', manufacturer: '', modelNumber: '', yearReleased: null, isActive: true,
      wattage: null, formFactor: '', efficiencyRating: '', pcie8pinConnectors: null,
      pcie12pinConnectors: null, pcie16pinConnectors: null, depthMm: null,
      modular: '', epsConnectors: null, fanSizeMm: null, isFanless: false,
    },
  });

  const [error, setError] = useState<string | null>(null);
  const numChange = (onChange: (v: number | null) => void) => (e: React.ChangeEvent<HTMLInputElement>) =>
    onChange(e.target.value === '' ? null : Number(e.target.value));

  async function onSubmit(data: PsuFormData) {
    setError(null);
    try {
      if (item) { await updatePsu(item.id, data); } else { await createPsu(data); }
      onSuccess();
    } catch (e) { setError(e instanceof Error ? e.message : 'An error occurred'); }
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          {([ ['name','Name *'], ['manufacturer','Manufacturer'], ['modelNumber','Model Number'],
              ['formFactor','Form Factor'], ['efficiencyRating','Efficiency Rating'], ['modular','Modular Type'],
          ] as [keyof PsuFormData, string][]).map(([name, label]) => (
            <FormField key={name} control={form.control} name={name}
              render={({ field }) => (
                <FormItem><FormLabel>{label}</FormLabel>
                  <FormControl><Input {...field} value={field.value as string ?? ''} /></FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          ))}
          {([ ['yearReleased','Year'], ['wattage','Wattage (W)'], ['pcie8pinConnectors','PCIe 8-pin'],
              ['pcie12pinConnectors','PCIe 12-pin'], ['pcie16pinConnectors','PCIe 16-pin'],
              ['depthMm','Depth (mm)'], ['epsConnectors','EPS Connectors'], ['fanSizeMm','Fan Size (mm)'],
          ] as [keyof PsuFormData, string][]).map(([name, label]) => (
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
        </div>
        <div className="flex gap-6">
          {([ ['isFanless','Fanless'], ['isActive','Active'] ] as [keyof PsuFormData, string][]).map(([name, label]) => (
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

export function PsuTable({ data }: { data: PsuWithPart[] }) {
  const router = useRouter();
  const [selected, setSelected] = useState<PsuWithPart | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const [, startTransition] = useTransition();

  const handleSuccess = () => { setDialogOpen(false); router.refresh(); };
  const handleDelete = (id: string) => {
    startTransition(async () => { await deletePsu(id); setDeleteId(null); router.refresh(); });
  };

  const columns: ColumnDef<PsuWithPart>[] = [
    { id: 'name', accessorFn: (r) => r.pcPart.name, header: 'Name', enableSorting: true },
    { id: 'manufacturer', accessorFn: (r) => r.pcPart.manufacturer ?? '', header: 'Manufacturer' },
    { accessorKey: 'wattage', header: 'Wattage (W)', enableSorting: true },
    { accessorKey: 'efficiencyRating', header: 'Efficiency', enableSorting: true },
    { accessorKey: 'modular', header: 'Modular', enableSorting: true },
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
        <div><h1 className="text-2xl font-bold">PSUs</h1><p className="text-muted-foreground text-sm mt-1">{data.length} total</p></div>
        <Button onClick={() => { setSelected(null); setDialogOpen(true); }}>New PSU</Button>
      </div>
      <DataTable columns={columns} data={data} filterPlaceholder="Filter PSUs..." />
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader><DialogTitle>{selected ? 'Edit PSU' : 'New PSU'}</DialogTitle></DialogHeader>
          <PsuForm item={selected} onSuccess={handleSuccess} />
        </DialogContent>
      </Dialog>
      <AlertDialog open={!!deleteId} onOpenChange={(open) => !open && setDeleteId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader><AlertDialogTitle>Delete PSU?</AlertDialogTitle><AlertDialogDescription>This action cannot be undone.</AlertDialogDescription></AlertDialogHeader>
          <AlertDialogFooter><AlertDialogCancel>Cancel</AlertDialogCancel><AlertDialogAction onClick={() => deleteId && handleDelete(deleteId)}>Delete</AlertDialogAction></AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
