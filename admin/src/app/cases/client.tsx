'use client';

import { useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import type { ColumnDef } from '@tanstack/react-table';
import type { PcCase, PcPart } from '@prisma/client';
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
import { createCase, updateCase, deleteCase, type CaseFormData } from './actions';

type CaseWithPart = PcCase & { pcPart: PcPart };

const schema = z.object({
  name: z.string().min(1), manufacturer: z.string(), modelNumber: z.string(),
  yearReleased: z.coerce.number().int().nullable(), isActive: z.boolean(),
  supportedMoboFormFactorsInput: z.string(), maxGpuLengthMm: z.coerce.number().int().nullable(),
  maxCoolerHeightMm: z.coerce.number().int().nullable(), maxRadiatorFrontMm: z.coerce.number().int().nullable(),
  maxRadiatorTopMm: z.coerce.number().int().nullable(), maxPsuLengthMm: z.coerce.number().int().nullable(),
  includedFanCount: z.coerce.number().int().nullable(), chamberCount: z.coerce.number().int().nullable(),
  frontPanelMesh: z.boolean(), color: z.string(), size: z.string(),
  driveBays35: z.coerce.number().int().nullable(), driveBays25: z.coerce.number().int().nullable(),
  maxFanSlots: z.coerce.number().int().nullable(), hasGlassPanel: z.boolean(),
  weightKg: z.coerce.number().nullable(), lengthMm: z.coerce.number().int().nullable(),
  widthMm: z.coerce.number().int().nullable(), heightMm: z.coerce.number().int().nullable(),
  usbFrontTypeA: z.coerce.number().int().nullable(), usbFrontTypeC: z.coerce.number().int().nullable(),
});

function CaseForm({ item, onSuccess }: { item: CaseWithPart | null; onSuccess: () => void }) {
  const form = useForm<CaseFormData>({
    resolver: zodResolver(schema),
    defaultValues: item ? {
      name: item.pcPart.name, manufacturer: item.pcPart.manufacturer ?? '',
      modelNumber: item.pcPart.modelNumber ?? '', yearReleased: item.pcPart.yearReleased,
      isActive: item.pcPart.isActive,
      supportedMoboFormFactorsInput: joinCommaList(item.supportedMoboFormFactors),
      maxGpuLengthMm: item.maxGpuLengthMm, maxCoolerHeightMm: item.maxCoolerHeightMm,
      maxRadiatorFrontMm: item.maxRadiatorFrontMm, maxRadiatorTopMm: item.maxRadiatorTopMm,
      maxPsuLengthMm: item.maxPsuLengthMm, includedFanCount: item.includedFanCount,
      chamberCount: item.chamberCount, frontPanelMesh: item.frontPanelMesh,
      color: item.color ?? '', size: item.size ?? '', driveBays35: item.driveBays35,
      driveBays25: item.driveBays25, maxFanSlots: item.maxFanSlots, hasGlassPanel: item.hasGlassPanel,
      weightKg: item.weightKg, lengthMm: item.lengthMm, widthMm: item.widthMm,
      heightMm: item.heightMm, usbFrontTypeA: item.usbFrontTypeA, usbFrontTypeC: item.usbFrontTypeC,
    } : {
      name: '', manufacturer: '', modelNumber: '', yearReleased: null, isActive: true,
      supportedMoboFormFactorsInput: '', maxGpuLengthMm: null, maxCoolerHeightMm: null,
      maxRadiatorFrontMm: null, maxRadiatorTopMm: null, maxPsuLengthMm: null,
      includedFanCount: null, chamberCount: null, frontPanelMesh: false, color: '', size: '',
      driveBays35: null, driveBays25: null, maxFanSlots: null, hasGlassPanel: false,
      weightKg: null, lengthMm: null, widthMm: null, heightMm: null,
      usbFrontTypeA: null, usbFrontTypeC: null,
    },
  });

  const [error, setError] = useState<string | null>(null);
  const numChange = (onChange: (v: number | null) => void) => (e: React.ChangeEvent<HTMLInputElement>) =>
    onChange(e.target.value === '' ? null : Number(e.target.value));

  async function onSubmit(data: CaseFormData) {
    setError(null);
    try {
      if (item) { await updateCase(item.id, data); } else { await createCase(data); }
      onSuccess();
    } catch (e) { setError(e instanceof Error ? e.message : 'An error occurred'); }
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          {([ ['name','Name *'], ['manufacturer','Manufacturer'], ['modelNumber','Model Number'],
              ['color','Color'], ['size','Size (ATX/mATX/ITX)'],
          ] as [keyof CaseFormData, string][]).map(([name, label]) => (
            <FormField key={name} control={form.control} name={name}
              render={({ field }) => (
                <FormItem><FormLabel>{label}</FormLabel>
                  <FormControl><Input {...field} value={field.value as string ?? ''} /></FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          ))}
          {([ ['yearReleased','Year'], ['maxGpuLengthMm','Max GPU Length (mm)'],
              ['maxCoolerHeightMm','Max Cooler Height (mm)'], ['maxRadiatorFrontMm','Max Rad Front (mm)'],
              ['maxRadiatorTopMm','Max Rad Top (mm)'], ['maxPsuLengthMm','Max PSU Length (mm)'],
              ['includedFanCount','Included Fans'], ['chamberCount','Chambers'],
              ['driveBays35','3.5" Bays'], ['driveBays25','2.5" Bays'],
              ['maxFanSlots','Fan Slots'], ['weightKg','Weight (kg)'],
              ['lengthMm','Length (mm)'], ['widthMm','Width (mm)'], ['heightMm','Height (mm)'],
              ['usbFrontTypeA','Front USB-A'], ['usbFrontTypeC','Front USB-C'],
          ] as [keyof CaseFormData, string][]).map(([name, label]) => (
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
        <FormField control={form.control} name="supportedMoboFormFactorsInput"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Supported Form Factors (comma-separated, e.g. ATX, mATX, ITX)</FormLabel>
              <FormControl><Input {...field} /></FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <div className="flex gap-6">
          {([ ['frontPanelMesh','Mesh Front'], ['hasGlassPanel','Glass Panel'], ['isActive','Active'] ] as [keyof CaseFormData, string][]).map(([name, label]) => (
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

export function CaseTable({ data }: { data: CaseWithPart[] }) {
  const router = useRouter();
  const [selected, setSelected] = useState<CaseWithPart | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const [, startTransition] = useTransition();

  const handleSuccess = () => { setDialogOpen(false); router.refresh(); };
  const handleDelete = (id: string) => {
    startTransition(async () => { await deleteCase(id); setDeleteId(null); router.refresh(); });
  };

  const columns: ColumnDef<CaseWithPart>[] = [
    { id: 'name', accessorFn: (r) => r.pcPart.name, header: 'Name', enableSorting: true },
    { id: 'manufacturer', accessorFn: (r) => r.pcPart.manufacturer ?? '', header: 'Manufacturer' },
    { accessorKey: 'size', header: 'Size', enableSorting: true },
    { accessorKey: 'maxGpuLengthMm', header: 'Max GPU (mm)', enableSorting: true },
    { accessorKey: 'maxCoolerHeightMm', header: 'Max Cooler (mm)', enableSorting: true },
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
        <div><h1 className="text-2xl font-bold">Cases</h1><p className="text-muted-foreground text-sm mt-1">{data.length} total</p></div>
        <Button onClick={() => { setSelected(null); setDialogOpen(true); }}>New Case</Button>
      </div>
      <DataTable columns={columns} data={data} filterPlaceholder="Filter cases..." />
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-3xl">
          <DialogHeader><DialogTitle>{selected ? 'Edit Case' : 'New Case'}</DialogTitle></DialogHeader>
          <CaseForm item={selected} onSuccess={handleSuccess} />
        </DialogContent>
      </Dialog>
      <AlertDialog open={!!deleteId} onOpenChange={(open) => !open && setDeleteId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader><AlertDialogTitle>Delete Case?</AlertDialogTitle><AlertDialogDescription>This action cannot be undone.</AlertDialogDescription></AlertDialogHeader>
          <AlertDialogFooter><AlertDialogCancel>Cancel</AlertDialogCancel><AlertDialogAction onClick={() => deleteId && handleDelete(deleteId)}>Delete</AlertDialogAction></AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
