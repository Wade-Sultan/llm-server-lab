'use client';

import { useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import type { ColumnDef } from '@tanstack/react-table';
import type { Fan, PcPart } from '@prisma/client';
import { Pencil, Trash2 } from 'lucide-react';
import { DataTable } from '@/components/data-table';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog';
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { Checkbox } from '@/components/ui/checkbox';
import { createFan, updateFan, deleteFan, type FanFormData } from './actions';

type FanWithPart = Fan & { pcPart: PcPart };

const schema = z.object({
  name: z.string().min(1), manufacturer: z.string(), modelNumber: z.string(),
  yearReleased: z.coerce.number().int().nullable(), isActive: z.boolean(),
  sizeMm: z.coerce.number().int().nullable(), maxRpm: z.coerce.number().int().nullable(),
  airflowCfm: z.coerce.number().nullable(), noiseDba: z.coerce.number().nullable(),
  isPwm: z.boolean(), hasRgb: z.boolean(), bearingType: z.string(),
  isStaticPressure: z.boolean(), packCount: z.coerce.number().int().nullable(),
});

function FanForm({ item, onSuccess }: { item: FanWithPart | null; onSuccess: () => void }) {
  const form = useForm<FanFormData>({
    resolver: zodResolver(schema),
    defaultValues: item ? {
      name: item.pcPart.name, manufacturer: item.pcPart.manufacturer ?? '',
      modelNumber: item.pcPart.modelNumber ?? '', yearReleased: item.pcPart.yearReleased,
      isActive: item.pcPart.isActive, sizeMm: item.sizeMm, maxRpm: item.maxRpm,
      airflowCfm: item.airflowCfm, noiseDba: item.noiseDba, isPwm: item.isPwm,
      hasRgb: item.hasRgb, bearingType: item.bearingType ?? '', isStaticPressure: item.isStaticPressure,
      packCount: item.packCount,
    } : {
      name: '', manufacturer: '', modelNumber: '', yearReleased: null, isActive: true,
      sizeMm: null, maxRpm: null, airflowCfm: null, noiseDba: null,
      isPwm: false, hasRgb: false, bearingType: '', isStaticPressure: false, packCount: null,
    },
  });

  const [error, setError] = useState<string | null>(null);
  const numChange = (onChange: (v: number | null) => void) => (e: React.ChangeEvent<HTMLInputElement>) =>
    onChange(e.target.value === '' ? null : Number(e.target.value));

  async function onSubmit(data: FanFormData) {
    setError(null);
    try {
      if (item) { await updateFan(item.id, data); } else { await createFan(data); }
      onSuccess();
    } catch (e) { setError(e instanceof Error ? e.message : 'An error occurred'); }
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          {([ ['name','Name *'], ['manufacturer','Manufacturer'], ['modelNumber','Model Number'], ['bearingType','Bearing Type'] ] as [keyof FanFormData, string][]).map(([name, label]) => (
            <FormField key={name} control={form.control} name={name}
              render={({ field }) => (
                <FormItem><FormLabel>{label}</FormLabel>
                  <FormControl><Input {...field} value={field.value as string ?? ''} /></FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          ))}
          {([ ['yearReleased','Year'], ['sizeMm','Size (mm)'], ['maxRpm','Max RPM'],
              ['airflowCfm','Airflow (CFM)'], ['noiseDba','Noise (dBA)'], ['packCount','Pack Count'],
          ] as [keyof FanFormData, string][]).map(([name, label]) => (
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
        <div className="flex gap-6">
          {([ ['isPwm','PWM'], ['hasRgb','RGB'], ['isStaticPressure','Static Pressure'], ['isActive','Active'] ] as [keyof FanFormData, string][]).map(([name, label]) => (
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

export function FanTable({ data }: { data: FanWithPart[] }) {
  const router = useRouter();
  const [selected, setSelected] = useState<FanWithPart | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const [, startTransition] = useTransition();

  const handleSuccess = () => { setDialogOpen(false); router.refresh(); };
  const handleDelete = (id: string) => {
    startTransition(async () => { await deleteFan(id); setDeleteId(null); router.refresh(); });
  };

  const columns: ColumnDef<FanWithPart>[] = [
    { id: 'name', accessorFn: (r) => r.pcPart.name, header: 'Name', enableSorting: true },
    { id: 'manufacturer', accessorFn: (r) => r.pcPart.manufacturer ?? '', header: 'Manufacturer' },
    { accessorKey: 'sizeMm', header: 'Size (mm)', enableSorting: true },
    { accessorKey: 'maxRpm', header: 'Max RPM', enableSorting: true },
    { accessorKey: 'noiseDba', header: 'Noise (dBA)', enableSorting: true },
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
        <div><h1 className="text-2xl font-bold">Fans</h1><p className="text-muted-foreground text-sm mt-1">{data.length} total</p></div>
        <Button onClick={() => { setSelected(null); setDialogOpen(true); }}>New Fan</Button>
      </div>
      <DataTable columns={columns} data={data} filterPlaceholder="Filter fans..." />
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader><DialogTitle>{selected ? 'Edit Fan' : 'New Fan'}</DialogTitle></DialogHeader>
          <FanForm item={selected} onSuccess={handleSuccess} />
        </DialogContent>
      </Dialog>
      <AlertDialog open={!!deleteId} onOpenChange={(open) => !open && setDeleteId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader><AlertDialogTitle>Delete Fan?</AlertDialogTitle><AlertDialogDescription>This action cannot be undone.</AlertDialogDescription></AlertDialogHeader>
          <AlertDialogFooter><AlertDialogCancel>Cancel</AlertDialogCancel><AlertDialogAction onClick={() => deleteId && handleDelete(deleteId)}>Delete</AlertDialogAction></AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
