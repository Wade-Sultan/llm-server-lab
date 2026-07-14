'use client';

import { useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import type { ColumnDef } from '@tanstack/react-table';
import type { ReferenceBuild } from '@prisma/client';
import { Pencil, Trash2, Plus, X } from 'lucide-react';
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

// A selectable option — a specific part (non-grouped) or a group (grouped).
type Option = { id: string; name: string; streetPriceCents: number | null };
type PartOption = Option & { partType: string };

type BuildPartRow = {
  partId: string;
  component: string;
  part: {
    id: string; name: string; partType: string;
    gpu: { gpuChipsetId: string } | null;
    ramKit: { ramGroupId: string } | null;
    storageDrive: { storageGroupId: string } | null;
    psu: { psuGroupId: string } | null;
  };
};
type BuildWithParts = ReferenceBuild & { parts: BuildPartRow[] };

const RESOLUTIONS = [1080, 1440, 2160] as const;

const schema = z.object({
  buildKey: z.string().min(1, 'Build key is required'),
  label: z.string().min(1, 'Label is required'),
  description: z.string(),
  isActive: z.boolean(),
  maxResolution: z.number().nullable(),
  cpuId: z.string().nullable(),
  motherboardId: z.string().nullable(),
  caseId: z.string().nullable(),
  cpuCoolerId: z.string().nullable(),
  fanIds: z.array(z.string()),
  ramGroupId: z.string().nullable(),
  psuGroupId: z.string().nullable(),
  gpuChipsetIds: z.array(z.string()),
  storageGroupIds: z.array(z.string()),
});

function fmtPrice(cents: number | null) {
  if (cents == null) return '';
  return ` — $${(cents / 100).toFixed(2)}`;
}

function buildDefaults(item: BuildWithParts | null): ReferenceBuildFormData {
  if (!item) {
    return {
      buildKey: '', label: '', description: '', isActive: true, maxResolution: null,
      cpuId: null, motherboardId: null, caseId: null, cpuCoolerId: null, fanIds: [],
      ramGroupId: null, psuGroupId: null, gpuChipsetIds: [], storageGroupIds: [],
    };
  }
  const partOf = (comp: string) => item.parts.find(p => p.component === comp)?.partId ?? null;
  const partsOf = (comp: string) => item.parts.filter(p => p.component === comp);
  return {
    buildKey: item.buildKey,
    label: item.label,
    description: item.description ?? '',
    isActive: item.isActive,
    maxResolution: item.maxResolution ?? null,
    cpuId: partOf('cpu'),
    motherboardId: partOf('motherboard'),
    caseId: partOf('case'),
    cpuCoolerId: partOf('cpucooler'),
    fanIds: partsOf('fan').map(p => p.partId),
    // Grouped slots pre-select the group the frozen member belongs to.
    ramGroupId: partsOf('ram')[0]?.part.ramKit?.ramGroupId ?? null,
    psuGroupId: partsOf('psu')[0]?.part.psu?.psuGroupId ?? null,
    gpuChipsetIds: partsOf('gpu').map(p => p.part.gpu?.gpuChipsetId).filter((x): x is string => !!x),
    storageGroupIds: partsOf('storage').map(p => p.part.storageDrive?.storageGroupId).filter((x): x is string => !!x),
  };
}

function OptionSelect({
  label, options, value, onChange,
}: {
  label: string;
  options: Option[];
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
            <SelectItem key={p.id} value={p.id}>{p.name}{fmtPrice(p.streetPriceCents)}</SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

function MultiOptionSelect({
  label, options, selected, onChange,
}: {
  label: string;
  options: Option[];
  selected: string[];
  onChange: (v: string[]) => void;
}) {
  const setAt = (i: number, id: string) => onChange(selected.map((s, idx) => (idx === i ? id : s)));
  const addSlot = () => onChange([...selected, options[0]?.id ?? '']);
  const removeSlot = (i: number) => onChange(selected.filter((_, idx) => idx !== i));

  return (
    <div className="space-y-1">
      <p className="text-sm font-medium">{label}</p>
      {options.length === 0 ? (
        <p className="text-xs text-muted-foreground">No eligible options</p>
      ) : (
        <div className="space-y-2">
          {selected.length === 0 && <p className="text-xs text-muted-foreground">None selected</p>}
          {selected.map((id, i) => (
            <div key={i} className="flex items-center gap-2">
              <Select value={id} onValueChange={v => setAt(i, v)}>
                <SelectTrigger className="flex-1">
                  <SelectValue placeholder={`Select ${label} ${i + 1}…`} />
                </SelectTrigger>
                <SelectContent>
                  {options.map(p => (
                    <SelectItem key={p.id} value={p.id}>{p.name}{fmtPrice(p.streetPriceCents)}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button type="button" variant="ghost" size="icon" onClick={() => removeSlot(i)}>
                <X className="h-3.5 w-3.5" />
              </Button>
            </div>
          ))}
          <Button type="button" variant="outline" size="sm" onClick={addSlot}>
            <Plus className="h-3.5 w-3.5" /> Add {label} {selected.length + 1}
          </Button>
        </div>
      )}
    </div>
  );
}

type Groups = { gpuChipsets: Option[]; ramGroups: Option[]; storageGroups: Option[]; psuGroups: Option[] };

function BuildForm({
  item, partOptions, groups, onSuccess,
}: {
  item: BuildWithParts | null;
  partOptions: PartOption[];
  groups: Groups;
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
      if (item) { await updateReferenceBuild(item.id, data); } else { await createReferenceBuild(data); }
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
                <FormControl><SelectTrigger><SelectValue placeholder="Select max resolution…" /></SelectTrigger></FormControl>
                <SelectContent>
                  <SelectItem value="__none">— None —</SelectItem>
                  {RESOLUTIONS.map(r => <SelectItem key={r} value={String(r)}>{r}p</SelectItem>)}
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
          <p className="text-xs text-muted-foreground -mt-2">
            GPU / RAM / Storage / PSU pick a <strong>group</strong>; a random member is frozen in on save.
            Re-save to re-roll.
          </p>

          <div className="grid grid-cols-2 gap-4">
            <FormField control={form.control} name="cpuId"
              render={({ field }) => (
                <FormItem><OptionSelect label="CPU" options={byType('cpu')} value={field.value} onChange={field.onChange} /><FormMessage /></FormItem>
              )}
            />
            <FormField control={form.control} name="motherboardId"
              render={({ field }) => (
                <FormItem><OptionSelect label="Motherboard" options={byType('motherboard')} value={field.value} onChange={field.onChange} /><FormMessage /></FormItem>
              )}
            />
            <FormField control={form.control} name="ramGroupId"
              render={({ field }) => (
                <FormItem><OptionSelect label="RAM Group" options={groups.ramGroups} value={field.value} onChange={field.onChange} /><FormMessage /></FormItem>
              )}
            />
            <FormField control={form.control} name="psuGroupId"
              render={({ field }) => (
                <FormItem><OptionSelect label="PSU Group" options={groups.psuGroups} value={field.value} onChange={field.onChange} /><FormMessage /></FormItem>
              )}
            />
            <FormField control={form.control} name="caseId"
              render={({ field }) => (
                <FormItem><OptionSelect label="Case" options={byType('case')} value={field.value} onChange={field.onChange} /><FormMessage /></FormItem>
              )}
            />
            <FormField control={form.control} name="cpuCoolerId"
              render={({ field }) => (
                <FormItem><OptionSelect label="CPU Cooler" options={byType('cpucooler')} value={field.value} onChange={field.onChange} /><FormMessage /></FormItem>
              )}
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <FormField control={form.control} name="gpuChipsetIds"
              render={({ field }) => (
                <FormItem><MultiOptionSelect label="GPU Chipset" options={groups.gpuChipsets} selected={field.value} onChange={field.onChange} /><FormMessage /></FormItem>
              )}
            />
            <FormField control={form.control} name="storageGroupIds"
              render={({ field }) => (
                <FormItem><MultiOptionSelect label="Storage Group" options={groups.storageGroups} selected={field.value} onChange={field.onChange} /><FormMessage /></FormItem>
              )}
            />
            <FormField control={form.control} name="fanIds"
              render={({ field }) => (
                <FormItem><MultiOptionSelect label="Fan" options={byType('fan')} selected={field.value} onChange={field.onChange} /><FormMessage /></FormItem>
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
  builds, partOptions, gpuChipsets, ramGroups, storageGroups, psuGroups,
}: {
  builds: BuildWithParts[];
  partOptions: PartOption[];
  gpuChipsets: Option[];
  ramGroups: Option[];
  storageGroups: Option[];
  psuGroups: Option[];
}) {
  const router = useRouter();
  const [selected, setSelected] = useState<BuildWithParts | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const [, startTransition] = useTransition();

  const groups: Groups = { gpuChipsets, ramGroups, storageGroups, psuGroups };
  const handleSuccess = () => { setDialogOpen(false); router.refresh(); };
  const handleDelete = (id: string) => {
    startTransition(async () => { await deleteReferenceBuild(id); setDeleteId(null); router.refresh(); });
  };

  const columns: ColumnDef<BuildWithParts>[] = [
    { accessorKey: 'buildKey', header: 'Build Key', enableSorting: true },
    { accessorKey: 'label', header: 'Label', enableSorting: true },
    {
      id: 'parts', header: 'Parts',
      cell: ({ row }) => {
        const parts = row.original.parts;
        if (!parts.length) return <span className="text-muted-foreground text-xs">None</span>;
        const byRole = (r: string) => parts.filter(p => p.component === r);
        const tags = [
          byRole('cpu')[0]?.part.name,
          byRole('gpu').length > 1 ? `${byRole('gpu').length}× GPU` : byRole('gpu')[0]?.part.name,
          byRole('ram')[0]?.part.name,
          byRole('storage').length > 1 ? `${byRole('storage').length}× Storage` : byRole('storage')[0]?.part.name,
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
          <BuildForm item={selected} partOptions={partOptions} groups={groups} onSuccess={handleSuccess} />
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
