'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import type { ColumnDef } from '@tanstack/react-table';
import type { PsuGroup } from '@prisma/client';
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
import { Checkbox } from '@/components/ui/checkbox';
import { centsToUsd, formatUsd } from '@/lib/utils';
import { createPsuGroup, updatePsuGroup, deletePsuGroup, type PsuGroupFormData } from './actions';

const schema = z.object({
  name: z.string().min(1, 'Name is required'),
  streetPriceUsd: z.coerce.number().nullable(),
  wattage: z.coerce.number().int().nullable(),
  formFactor: z.string(),
  efficiencyRating: z.string(),
  modular: z.string(),
  isFanless: z.boolean(),
  fanSizeMm: z.coerce.number().int().nullable(),
  pcie8pinConnectors: z.coerce.number().int().nullable(),
  pcie12pinConnectors: z.coerce.number().int().nullable(),
  pcie16pinConnectors: z.coerce.number().int().nullable(),
  epsConnectors: z.coerce.number().int().nullable(),
});

function PsuGroupForm({ item, onSuccess }: { item: PsuGroup | null; onSuccess: () => void }) {
  const form = useForm<PsuGroupFormData>({
    resolver: zodResolver(schema),
    defaultValues: item ? {
      name: item.name,
      streetPriceUsd: centsToUsd(item.streetPriceCents),
      wattage: item.wattage,
      formFactor: item.formFactor ?? '',
      efficiencyRating: item.efficiencyRating ?? '',
      modular: item.modular ?? '',
      isFanless: item.isFanless,
      fanSizeMm: item.fanSizeMm,
      pcie8pinConnectors: item.pcie8pinConnectors,
      pcie12pinConnectors: item.pcie12pinConnectors,
      pcie16pinConnectors: item.pcie16pinConnectors,
      epsConnectors: item.epsConnectors,
    } : {
      name: '', streetPriceUsd: null, wattage: null, formFactor: '', efficiencyRating: '', modular: '', isFanless: false,
      fanSizeMm: null, pcie8pinConnectors: null, pcie12pinConnectors: null,
      pcie16pinConnectors: null, epsConnectors: null,
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

  async function onSubmit(data: PsuGroupFormData) {
    setError(null);
    try {
      if (item) { await updatePsuGroup(item.id, data); } else { await createPsuGroup(data); }
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
            ['name', 'Name * (e.g. "850W 80+ Gold ATX")'], ['formFactor', 'Form Factor *'],
            ['efficiencyRating', 'Efficiency *'], ['modular', 'Modular'],
          ] as [keyof PsuGroupFormData, string][]).map(([name, label]) => (
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
            ['wattage', 'Wattage *'], ['fanSizeMm', 'Fan Size (mm)'], ['pcie8pinConnectors', 'PCIe 8-pin'],
            ['pcie12pinConnectors', 'PCIe 12-pin'], ['pcie16pinConnectors', 'PCIe 16-pin'], ['epsConnectors', 'EPS Connectors'],
          ] as [keyof PsuGroupFormData, string][]).map(([name, label]) => (
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
        <FormField control={form.control} name="isFanless"
          render={({ field }) => (
            <FormItem className="flex items-center gap-2 space-y-0">
              <FormControl><Checkbox checked={field.value} onCheckedChange={field.onChange} /></FormControl>
              <FormLabel>Fanless</FormLabel>
            </FormItem>
          )}
        />
        {error && <p className="text-sm text-destructive">{error}</p>}
        <div className="flex justify-end pt-2">
          <Button type="submit" disabled={form.formState.isSubmitting}>
            {form.formState.isSubmitting ? 'Saving...' : item ? 'Update Group' : 'Create Group'}
          </Button>
        </div>
      </form>
    </Form>
  );
}

export function PsuGroupTable({ data }: { data: PsuGroup[] }) {
  const router = useRouter();
  const [selected, setSelected] = useState<PsuGroup | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deleteId, setDeleteId] = useState<string | null>(null);

  const handleSuccess = () => { setDialogOpen(false); router.refresh(); };
  const handleDelete = async (id: string) => { await deletePsuGroup(id); setDeleteId(null); router.refresh(); };

  const columns: ColumnDef<PsuGroup>[] = [
    { accessorKey: 'name', header: 'Name', enableSorting: true },
    { accessorKey: 'wattage', header: 'Wattage', enableSorting: true },
    { accessorKey: 'efficiencyRating', header: 'Efficiency' },
    { accessorKey: 'formFactor', header: 'Form Factor' },
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
          <h1 className="text-2xl font-bold">PSU Groups</h1>
          <p className="text-muted-foreground text-sm mt-1">{data.length} total · shared spec for PSU units</p>
        </div>
        <Button onClick={() => { setSelected(null); setDialogOpen(true); }}>New Group</Button>
      </div>
      <DataTable columns={columns} data={data} filterPlaceholder="Filter groups..." />
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-3xl">
          <DialogHeader><DialogTitle>{selected ? 'Edit Group' : 'New Group'}</DialogTitle></DialogHeader>
          <PsuGroupForm item={selected} onSuccess={handleSuccess} />
        </DialogContent>
      </Dialog>
      <AlertDialog open={!!deleteId} onOpenChange={(open) => !open && setDeleteId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Group?</AlertDialogTitle>
            <AlertDialogDescription>
              This cannot be undone. Deleting fails if any PSU still references it.
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
