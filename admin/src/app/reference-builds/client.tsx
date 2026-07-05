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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { createReferenceBuild, updateReferenceBuild, deleteReferenceBuild, type ReferenceBuildFormData } from './actions';

type PartOption = { id: string; name: string; partType: string; streetPriceCents: number | null };
type BuildPart = { partId: string; part: PartOption };
type BuildWithParts = ReferenceBuild & { parts: BuildPart[] };

const RESOLUTIONS = [1080, 1440, 2160] as const;

const schema = z.object({
  buildKey: z.string().min(1, 'Build key is required'),
  label: z.string().min(1, 'Label is required'),
  description: z.string(),
  isActive: z.boolean(),
  maxResolution: z.number().nullable(),
  cpuId: z.string().nullable(),
  motherboardId: z.string().nullable(),
  ramId: z.string().nullable(),
  psuId: z.string().nullable(),
  caseId: z.string().nullable(),
  cpuCoolerId: z.string().nullable(),
  gpuIds: z.array(z.string()),
  storageIds: z.array(z.string()),
  fanIds: z.array(z.string()),
});

function fmtPrice(cents: number | null) {
  if (cents == null) return '';
  return ` — $${(cents / 100).toFixed(2)}`;
}

function buildDefaults(item: BuildWithParts | null): ReferenceBuildFormData {
  if (!item) {
    return {
      buildKey: '', label: '', description: '', isActive: true, maxResolution: null,
      cpuId: null, motherboardId: null, ramId: null, psuId: null,
      caseId: null, cpuCoolerId: null, gpuIds: [], storageIds: [], fanIds: [],
    };
  }
  const single = (type: string) => item.parts.find(p => p.part.partType === type)?.partId ?? null;
  const multi = (type: string) => item.parts.filter(p => p.part.partType === type).map(p => p.partId);
  return {
    buildKey: item.buildKey,
    label: item.label,
    description: item.description ?? '',
    isActive: item.isActive,
    maxResolution: item.maxResolution ?? null,
    cpuId: single('cpu'),
    motherboardId: single('motherboard'),
    ramId: single('ram'),
    psuId: single('psu'),
    caseId: single('case'),
    cpuCoolerId: single('cpucooler'),
    gpuIds: multi('gpu'),
    storageIds: multi('storage'),
    fanIds: multi('fan'),
  };
}

function PartSelect({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: PartOption[];
  value: string | null;
  onChange: (v: string | null) => void;
}) {
  return (
    <div className="space-y-1">
      <p className="text-sm font-medium">{label}</p>
      <Select value={value ?? '__none'} onValueChange={v => onChange(v === '__none' ? null : v)}>
        <SelectTrigger>
          <SelectValue placeholder={`Select ${label}…`} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="__none">— None —</SelectItem>
          {options.map(p => (
            <SelectItem key={p.id} value={p.id}>
              {p.name}{fmtPrice(p.streetPriceCents)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

function MultiPartSelect({
  label,
  options,
  selected,
  onChange,
}: {
  label: string;
  options: PartOption[];
  selected: string[];
  onChange: (v: string[]) => void;
}) {
  const toggle = (id: string, checked: boolean) =>
    onChange(checked ? [...selected, id] : selected.filter(s => s !== id));

  return (
    <div className="space-y-1">
      <p className="text-sm font-medium">{label}</p>
      {options.length === 0 ? (
        <p className="text-xs text-muted-foreground">No eligible parts</p>
      ) : (
        <div className="border rounded-md divide-y max-h-40 overflow-y-auto">
          {options.map(p => (
            <label key={p.id} className="flex items-center gap-2 px-3 py-2 cursor-pointer hover:bg-accent text-sm">
              <Checkbox
                checked={selected.includes(p.id)}
                onCheckedChange={checked => toggle(p.id, !!checked)}
              />
              <span>{p.name}{fmtPrice(p.streetPriceCents)}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

function BuildForm({
  item,
  partOptions,
  onSuccess,
}: {
  item: BuildWithParts | null;
  partOptions: PartOption[];
  onSuccess: () => void;
}) {
  const byType = (type: string) => partOptions.filter(p => p.partType === type);

  const form = useForm<ReferenceBuildFormData>({
    resolver: zodResolver(schema),
    defaultValues: buildDefaults(item),
  });

  const [error, setError] = useState<string | null>(null);

  async function onSubmit(data: ReferenceBuildFormData) {
    setError(null);
    try {
      if (item) {
        await updateReferenceBuild(item.id, data);
      } else {
        await createReferenceBuild(data);
      }
      onSuccess();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'An error occurred');
    }
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-5">
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
        </div>

        <FormField control={form.control} name="description"
          render={({ field }) => (
            <FormItem><FormLabel>Description</FormLabel>
              <FormControl><Textarea rows={3} {...field} /></FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField control={form.control} name="maxResolution"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Max Resolution</FormLabel>
              <Select
                value={field.value == null ? '__none' : String(field.value)}
                onValueChange={v => field.onChange(v === '__none' ? null : Number(v))}
              >
                <FormControl>
                  <SelectTrigger>
                    <SelectValue placeholder="Select max resolution…" />
                  </SelectTrigger>
                </FormControl>
                <SelectContent>
                  <SelectItem value="__none">— None —</SelectItem>
                  {RESOLUTIONS.map(r => (
                    <SelectItem key={r} value={String(r)}>{r}p</SelectItem>
                  ))}
                </SelectContent>
              </Select>
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

        <div className="border-t pt-4 space-y-4">
          <p className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">Parts</p>

          <div className="grid grid-cols-2 gap-4">
            <FormField control={form.control} name="cpuId"
              render={({ field }) => (
                <FormItem>
                  <PartSelect label="CPU" options={byType('cpu')} value={field.value} onChange={field.onChange} />
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField control={form.control} name="motherboardId"
              render={({ field }) => (
                <FormItem>
                  <PartSelect label="Motherboard" options={byType('motherboard')} value={field.value} onChange={field.onChange} />
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField control={form.control} name="ramId"
              render={({ field }) => (
                <FormItem>
                  <PartSelect label="RAM" options={byType('ram')} value={field.value} onChange={field.onChange} />
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField control={form.control} name="psuId"
              render={({ field }) => (
                <FormItem>
                  <PartSelect label="PSU" options={byType('psu')} value={field.value} onChange={field.onChange} />
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField control={form.control} name="caseId"
              render={({ field }) => (
                <FormItem>
                  <PartSelect label="Case" options={byType('case')} value={field.value} onChange={field.onChange} />
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField control={form.control} name="cpuCoolerId"
              render={({ field }) => (
                <FormItem>
                  <PartSelect label="CPU Cooler" options={byType('cpucooler')} value={field.value} onChange={field.onChange} />
                  <FormMessage />
                </FormItem>
              )}
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <FormField control={form.control} name="gpuIds"
              render={({ field }) => (
                <FormItem>
                  <MultiPartSelect label="GPU (multi)" options={byType('gpu')} selected={field.value} onChange={field.onChange} />
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField control={form.control} name="storageIds"
              render={({ field }) => (
                <FormItem>
                  <MultiPartSelect label="Storage (multi)" options={byType('storage')} selected={field.value} onChange={field.onChange} />
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField control={form.control} name="fanIds"
              render={({ field }) => (
                <FormItem>
                  <MultiPartSelect label="Fans (multi)" options={byType('fan')} selected={field.value} onChange={field.onChange} />
                  <FormMessage />
                </FormItem>
              )}
            />
          </div>
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

export function ReferenceBuildTable({
  builds,
  partOptions,
}: {
  builds: BuildWithParts[];
  partOptions: PartOption[];
}) {
  const router = useRouter();
  const [selected, setSelected] = useState<BuildWithParts | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const [, startTransition] = useTransition();

  const handleSuccess = () => { setDialogOpen(false); router.refresh(); };
  const handleDelete = (id: string) => {
    startTransition(async () => { await deleteReferenceBuild(id); setDeleteId(null); router.refresh(); });
  };

  const columns: ColumnDef<BuildWithParts>[] = [
    { accessorKey: 'buildKey', header: 'Build Key', enableSorting: true },
    { accessorKey: 'label', header: 'Label', enableSorting: true },
    {
      id: 'parts',
      header: 'Parts',
      cell: ({ row }) => {
        const parts = row.original.parts;
        if (!parts.length) return <span className="text-muted-foreground text-xs">None</span>;
        const byType = (t: string) => parts.filter(p => p.part.partType === t);
        const tags = [
          byType('cpu')[0]?.part.name,
          byType('gpu').length > 1 ? `${byType('gpu').length}× GPU` : byType('gpu')[0]?.part.name,
          byType('ram')[0]?.part.name,
          byType('storage').length > 1 ? `${byType('storage').length}× Storage` : byType('storage')[0]?.part.name,
        ].filter(Boolean);
        return <span className="text-xs text-muted-foreground">{tags.join(', ') || `${parts.length} parts`}</span>;
      },
    },
    {
      accessorKey: 'isActive', header: 'Active',
      cell: ({ getValue }) => <Badge variant={getValue<boolean>() ? 'default' : 'secondary'}>{getValue<boolean>() ? 'Active' : 'Inactive'}</Badge>,
    },
    {
      id: 'actions', header: '', cell: ({ row }) => (
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
          <h1 className="text-2xl font-bold">Reference Builds</h1>
          <p className="text-muted-foreground text-sm mt-1">{builds.length} total</p>
        </div>
        <Button onClick={() => { setSelected(null); setDialogOpen(true); }}>New Build</Button>
      </div>
      <DataTable columns={columns} data={builds} filterPlaceholder="Filter builds..." filterColumn="buildKey" />
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
          <DialogHeader><DialogTitle>{selected ? 'Edit Reference Build' : 'New Reference Build'}</DialogTitle></DialogHeader>
          <BuildForm item={selected} partOptions={partOptions} onSuccess={handleSuccess} />
        </DialogContent>
      </Dialog>
      <AlertDialog open={!!deleteId} onOpenChange={(open) => !open && setDeleteId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Reference Build?</AlertDialogTitle>
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
