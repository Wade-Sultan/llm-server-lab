'use client';

import { useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import type { ColumnDef } from '@tanstack/react-table';
import type { ReferenceBuild } from '@prisma/client';
import { Pencil, Trash2 } from 'lucide-react';
import { DataTable } from '@/components/data-table';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog';
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Checkbox } from '@/components/ui/checkbox';
import { createReferenceBuild, updateReferenceBuild, deleteReferenceBuild, type ReferenceBuildFormData } from './actions';

const schema = z.object({
  buildKey: z.string().min(1, 'Build key is required'),
  label: z.string().min(1, 'Label is required'),
  description: z.string(),
  totalApprox: z.coerce.number().int().nullable(),
  isActive: z.boolean(),
});

function BuildForm({ item, onSuccess }: { item: ReferenceBuild | null; onSuccess: () => void }) {
  const form = useForm<ReferenceBuildFormData>({
    resolver: zodResolver(schema),
    defaultValues: item ? {
      buildKey: item.buildKey, label: item.label, description: item.description ?? '',
      totalApprox: item.totalApprox, isActive: item.isActive,
    } : {
      buildKey: '', label: '', description: '', totalApprox: null, isActive: true,
    },
  });

  const [error, setError] = useState<string | null>(null);

  async function onSubmit(data: ReferenceBuildFormData) {
    setError(null);
    try {
      if (item) { await updateReferenceBuild(item.id, data); } else { await createReferenceBuild(data); }
      onSuccess();
    } catch (e) { setError(e instanceof Error ? e.message : 'An error occurred'); }
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <FormField control={form.control} name="buildKey"
            render={({ field }) => (
              <FormItem><FormLabel>Build Key *</FormLabel>
                <FormControl><Input {...field} placeholder="e.g. budget-gaming-2025" /></FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField control={form.control} name="label"
            render={({ field }) => (
              <FormItem><FormLabel>Label *</FormLabel>
                <FormControl><Input {...field} placeholder="e.g. Budget Gaming Build" /></FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField control={form.control} name="totalApprox"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Approx. Price (cents)</FormLabel>
                <FormControl>
                  <Input type="number" value={field.value ?? ''} onChange={e => field.onChange(e.target.value === '' ? null : Number(e.target.value))} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
        </div>
        <FormField control={form.control} name="description"
          render={({ field }) => (
            <FormItem><FormLabel>Description</FormLabel>
              <FormControl><Textarea rows={4} {...field} /></FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
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
            {form.formState.isSubmitting ? 'Saving...' : item ? 'Update' : 'Create'}
          </Button>
        </div>
      </form>
    </Form>
  );
}

export function ReferenceBuildTable({ data }: { data: ReferenceBuild[] }) {
  const router = useRouter();
  const [selected, setSelected] = useState<ReferenceBuild | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const [, startTransition] = useTransition();

  const handleSuccess = () => { setDialogOpen(false); router.refresh(); };
  const handleDelete = (id: string) => {
    startTransition(async () => { await deleteReferenceBuild(id); setDeleteId(null); router.refresh(); });
  };

  const columns: ColumnDef<ReferenceBuild>[] = [
    { accessorKey: 'buildKey', header: 'Build Key', enableSorting: true },
    { accessorKey: 'label', header: 'Label', enableSorting: true },
    { accessorKey: 'totalApprox', header: 'Price (cents)', enableSorting: true,
      cell: ({ getValue }) => getValue<number | null>() != null ? `$${((getValue<number>()) / 100).toFixed(2)}` : '—' },
    { accessorKey: 'isActive', header: 'Active',
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
        <div><h1 className="text-2xl font-bold">Reference Builds</h1><p className="text-muted-foreground text-sm mt-1">{data.length} total</p></div>
        <Button onClick={() => { setSelected(null); setDialogOpen(true); }}>New Build</Button>
      </div>
      <DataTable columns={columns} data={data} filterPlaceholder="Filter builds..." filterColumn="buildKey" />
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>{selected ? 'Edit Reference Build' : 'New Reference Build'}</DialogTitle></DialogHeader>
          <BuildForm item={selected} onSuccess={handleSuccess} />
        </DialogContent>
      </Dialog>
      <AlertDialog open={!!deleteId} onOpenChange={(open) => !open && setDeleteId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader><AlertDialogTitle>Delete Reference Build?</AlertDialogTitle><AlertDialogDescription>This action cannot be undone.</AlertDialogDescription></AlertDialogHeader>
          <AlertDialogFooter><AlertDialogCancel>Cancel</AlertDialogCancel><AlertDialogAction onClick={() => deleteId && handleDelete(deleteId)}>Delete</AlertDialogAction></AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
