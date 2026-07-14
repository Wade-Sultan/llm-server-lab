'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import type { ColumnDef } from '@tanstack/react-table';
import type { RamGroup } from '@prisma/client';
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
import { createRamGroup, updateRamGroup, deleteRamGroup, type RamGroupFormData } from './actions';

const schema = z.object({
  name: z.string().min(1, 'Name is required'),
  ddrGeneration: z.string(),
  speedMhz: z.coerce.number().int().nullable(),
  capacityGb: z.coerce.number().int().nullable(),
  modules: z.coerce.number().int().nullable(),
  moduleCapacityGb: z.coerce.number().int().nullable(),
  casLatency: z.coerce.number().int().nullable(),
  voltage: z.coerce.number().nullable(),
  isEcc: z.boolean(),
});

function RamGroupForm({ item, onSuccess }: { item: RamGroup | null; onSuccess: () => void }) {
  const form = useForm<RamGroupFormData>({
    resolver: zodResolver(schema),
    defaultValues: item ? {
      name: item.name,
      ddrGeneration: item.ddrGeneration ?? '',
      speedMhz: item.speedMhz,
      capacityGb: item.capacityGb,
      modules: item.modules,
      moduleCapacityGb: item.moduleCapacityGb,
      casLatency: item.casLatency,
      voltage: item.voltage,
      isEcc: item.isEcc,
    } : {
      name: '', ddrGeneration: '', speedMhz: null, capacityGb: null, modules: null,
      moduleCapacityGb: null, casLatency: null, voltage: null, isEcc: false,
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

  async function onSubmit(data: RamGroupFormData) {
    setError(null);
    try {
      if (item) { await updateRamGroup(item.id, data); } else { await createRamGroup(data); }
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
            ['name', 'Name * (e.g. "DDR5-6000 32GB (2x16)")'], ['ddrGeneration', 'DDR Generation *'],
          ] as [keyof RamGroupFormData, string][]).map(([name, label]) => (
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
            ['speedMhz', 'Speed (MHz) *'], ['capacityGb', 'Capacity (GB) *'], ['modules', 'Modules (kit count) *'],
            ['moduleCapacityGb', 'Per-Module (GB)'], ['casLatency', 'CAS Latency'], ['voltage', 'Voltage (V)'],
          ] as [keyof RamGroupFormData, string][]).map(([name, label]) => (
            <FormField key={name} control={form.control} name={name}
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{label}</FormLabel>
                  <FormControl>
                    <Input {...numField(field as { value: number | null; onChange: (v: number | null) => void })}
                      step={name === 'voltage' ? '0.01' : undefined} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          ))}
        </div>
        <FormField control={form.control} name="isEcc"
          render={({ field }) => (
            <FormItem className="flex items-center gap-2 space-y-0">
              <FormControl><Checkbox checked={field.value} onCheckedChange={field.onChange} /></FormControl>
              <FormLabel>ECC</FormLabel>
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

export function RamGroupTable({ data }: { data: RamGroup[] }) {
  const router = useRouter();
  const [selected, setSelected] = useState<RamGroup | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deleteId, setDeleteId] = useState<string | null>(null);

  const handleSuccess = () => { setDialogOpen(false); router.refresh(); };
  const handleDelete = async (id: string) => { await deleteRamGroup(id); setDeleteId(null); router.refresh(); };

  const columns: ColumnDef<RamGroup>[] = [
    { accessorKey: 'name', header: 'Name', enableSorting: true },
    { accessorKey: 'ddrGeneration', header: 'DDR' },
    { accessorKey: 'speedMhz', header: 'Speed (MHz)', enableSorting: true },
    { accessorKey: 'capacityGb', header: 'Capacity (GB)', enableSorting: true },
    { accessorKey: 'modules', header: 'Kit' },
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
          <h1 className="text-2xl font-bold">RAM Groups</h1>
          <p className="text-muted-foreground text-sm mt-1">{data.length} total · shared spec for RAM kits</p>
        </div>
        <Button onClick={() => { setSelected(null); setDialogOpen(true); }}>New Group</Button>
      </div>
      <DataTable columns={columns} data={data} filterPlaceholder="Filter groups..." />
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-3xl">
          <DialogHeader><DialogTitle>{selected ? 'Edit Group' : 'New Group'}</DialogTitle></DialogHeader>
          <RamGroupForm item={selected} onSuccess={handleSuccess} />
        </DialogContent>
      </Dialog>
      <AlertDialog open={!!deleteId} onOpenChange={(open) => !open && setDeleteId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Group?</AlertDialogTitle>
            <AlertDialogDescription>
              This cannot be undone. Deleting fails if any RAM kit still references it.
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
