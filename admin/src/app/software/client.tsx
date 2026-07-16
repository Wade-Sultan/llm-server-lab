'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { useFieldArray, useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import type { ColumnDef } from '@tanstack/react-table';
import type { Software, SoftwareTier } from '@prisma/client';
import { Pencil, Trash2, Plus, X } from 'lucide-react';
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
import { Textarea } from '@/components/ui/textarea';
import { Checkbox } from '@/components/ui/checkbox';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { joinCommaList } from '@/lib/utils';
import { createSoftware, updateSoftware, deleteSoftware, type SoftwareFormData } from './actions';

const CATEGORIES = ['creative', 'productivity', 'ai_ml', 'development', 'streaming'] as const;
const GPU_IMPORTANCE = ['required', 'accelerated', 'optional', 'irrelevant'] as const;

type SoftwareWithTiers = Software & { tiers: SoftwareTier[] };

const tierSchema = z.object({
  id: z.string().nullable(),
  name: z.string().min(1, 'Tier name required'),
  slug: z.string(),
  gpuImportance: z.string().min(1),
  minRamGb: z.coerce.number().int().nullable(),
  recommendedRamGb: z.coerce.number().int().nullable(),
  minVramGb: z.coerce.number().int().nullable(),
  minStorageGb: z.coerce.number().int().nullable(),
  minCores: z.coerce.number().int().nullable(),
  prefersSingleThread: z.boolean(),
  notes: z.string(),
});

const schema = z.object({
  name: z.string().min(1, 'Name is required'),
  slug: z.string(),
  category: z.string().min(1, 'Category is required'),
  useCaseTagsInput: z.string(),
  developer: z.string(),
  currentVersion: z.string(),
  websiteUrl: z.string(),
  imageUrl: z.string(),
  isFree: z.boolean(),
  platformRequirementsInput: z.string(),
  notes: z.string(),
  tiers: z.array(tierSchema),
});

function softwareDefaults(item: SoftwareWithTiers | null): SoftwareFormData {
  if (!item) {
    return {
      name: '', slug: '', category: 'creative', useCaseTagsInput: '', developer: '',
      currentVersion: '', websiteUrl: '', imageUrl: '', isFree: false,
      platformRequirementsInput: '', notes: '', tiers: [],
    };
  }
  return {
    name: item.name,
    slug: item.slug,
    category: item.category,
    useCaseTagsInput: joinCommaList(item.useCaseTags),
    developer: item.developer ?? '',
    currentVersion: item.currentVersion ?? '',
    websiteUrl: item.websiteUrl ?? '',
    imageUrl: item.imageUrl ?? '',
    isFree: item.isFree ?? false,
    platformRequirementsInput: joinCommaList(item.platformRequirements),
    notes: item.notes ?? '',
    tiers: item.tiers.map((t) => ({
      id: t.id,
      name: t.name,
      slug: t.slug,
      gpuImportance: t.gpuImportance,
      minRamGb: t.minRamGb,
      recommendedRamGb: t.recommendedRamGb,
      minVramGb: t.minVramGb,
      minStorageGb: t.minStorageGb,
      minCores: t.minCores,
      prefersSingleThread: t.prefersSingleThread ?? false,
      notes: t.notes ?? '',
    })),
  };
}

function SoftwareForm({ item, onSuccess }: { item: SoftwareWithTiers | null; onSuccess: () => void }) {
  const form = useForm<SoftwareFormData>({
    resolver: zodResolver(schema),
    defaultValues: softwareDefaults(item),
  });
  const tiers = useFieldArray({ control: form.control, name: 'tiers' });
  const [error, setError] = useState<string | null>(null);

  const numField = (field: { value: number | null; onChange: (v: number | null) => void }) => ({
    ...field,
    type: 'number' as const,
    value: field.value ?? '',
    onChange: (e: React.ChangeEvent<HTMLInputElement>) =>
      field.onChange(e.target.value === '' ? null : Number(e.target.value)),
  });

  async function onSubmit(data: SoftwareFormData) {
    setError(null);
    try {
      if (item) { await updateSoftware(item.id, data); } else { await createSoftware(data); }
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
            ['name', 'Name *'], ['slug', 'Slug (blank = from name)'], ['developer', 'Developer'],
            ['currentVersion', 'Current Version'], ['websiteUrl', 'Website URL'], ['imageUrl', 'Image URL'],
          ] as [keyof SoftwareFormData, string][]).map(([name, label]) => (
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
          <FormField control={form.control} name="category"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Category *</FormLabel>
                <Select value={field.value} onValueChange={field.onChange}>
                  <FormControl><SelectTrigger><SelectValue /></SelectTrigger></FormControl>
                  <SelectContent>
                    {CATEGORIES.map((c) => <SelectItem key={c} value={c}>{c}</SelectItem>)}
                  </SelectContent>
                </Select>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField control={form.control} name="useCaseTagsInput"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Use Case Tags (comma-separated)</FormLabel>
                <FormControl><Input {...field} placeholder="e.g. video_editing, streaming" /></FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
        </div>
        <FormField control={form.control} name="platformRequirementsInput"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Platform Requirements (comma-separated)</FormLabel>
              <FormControl><Input {...field} placeholder="e.g. windows, cuda" /></FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField control={form.control} name="notes"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Notes</FormLabel>
              <FormControl><Textarea rows={2} {...field} /></FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField control={form.control} name="isFree"
          render={({ field }) => (
            <FormItem className="flex items-center gap-2 space-y-0">
              <FormControl><Checkbox checked={field.value} onCheckedChange={field.onChange} /></FormControl>
              <FormLabel>Free</FormLabel>
            </FormItem>
          )}
        />

        <div className="border-t pt-4 space-y-3">
          <div className="flex items-center justify-between">
            <p className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
              Usage Tiers (e.g. &ldquo;1080p editing&rdquo; → &ldquo;8K editing&rdquo;)
            </p>
            <Button type="button" variant="outline" size="sm"
              onClick={() => tiers.append({
                id: null, name: '', slug: '', gpuImportance: 'optional',
                minRamGb: null, recommendedRamGb: null, minVramGb: null,
                minStorageGb: null, minCores: null, prefersSingleThread: false, notes: '',
              })}>
              <Plus className="h-3.5 w-3.5" /> Add Tier
            </Button>
          </div>
          {tiers.fields.map((row, i) => (
            <div key={row.id} className="border rounded-md p-3 space-y-2">
              <div className="grid grid-cols-[2fr_2fr_auto] gap-2 items-end">
                <FormField control={form.control} name={`tiers.${i}.name`}
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-xs">Tier Name *</FormLabel>
                      <FormControl><Input {...field} placeholder='e.g. "4K editing"' /></FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField control={form.control} name={`tiers.${i}.gpuImportance`}
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-xs">GPU Importance</FormLabel>
                      <Select value={field.value} onValueChange={field.onChange}>
                        <FormControl><SelectTrigger><SelectValue /></SelectTrigger></FormControl>
                        <SelectContent>
                          {GPU_IMPORTANCE.map((g) => <SelectItem key={g} value={g}>{g}</SelectItem>)}
                        </SelectContent>
                      </Select>
                    </FormItem>
                  )}
                />
                <Button type="button" variant="ghost" size="icon" onClick={() => tiers.remove(i)}>
                  <X className="h-3.5 w-3.5" />
                </Button>
              </div>
              <div className="grid grid-cols-5 gap-2">
                {([
                  ['minRamGb', 'Min RAM'], ['recommendedRamGb', 'Rec. RAM'], ['minVramGb', 'Min VRAM'],
                  ['minStorageGb', 'Storage'], ['minCores', 'Min Cores'],
                ] as const).map(([name, label]) => (
                  <FormField key={name} control={form.control} name={`tiers.${i}.${name}`}
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel className="text-xs">{label} (GB)</FormLabel>
                        <FormControl>
                          <Input {...numField(field as { value: number | null; onChange: (v: number | null) => void })} />
                        </FormControl>
                      </FormItem>
                    )}
                  />
                ))}
              </div>
              <div className="flex items-center gap-4">
                <FormField control={form.control} name={`tiers.${i}.prefersSingleThread`}
                  render={({ field }) => (
                    <FormItem className="flex items-center gap-2 space-y-0">
                      <FormControl><Checkbox checked={field.value} onCheckedChange={field.onChange} /></FormControl>
                      <FormLabel className="text-xs">Latency-bound (prefers single-thread speed)</FormLabel>
                    </FormItem>
                  )}
                />
                <FormField control={form.control} name={`tiers.${i}.notes`}
                  render={({ field }) => (
                    <FormItem className="flex-1">
                      <FormControl><Input {...field} placeholder="Notes…" /></FormControl>
                    </FormItem>
                  )}
                />
              </div>
            </div>
          ))}
        </div>

        {error && <p className="text-sm text-destructive">{error}</p>}
        <div className="flex justify-end pt-2">
          <Button type="submit" disabled={form.formState.isSubmitting}>
            {form.formState.isSubmitting ? 'Saving...' : item ? 'Update Software' : 'Create Software'}
          </Button>
        </div>
      </form>
    </Form>
  );
}

export function SoftwareTable({ software }: { software: SoftwareWithTiers[] }) {
  const router = useRouter();
  const [selected, setSelected] = useState<SoftwareWithTiers | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deleteId, setDeleteId] = useState<string | null>(null);

  const handleSuccess = () => { setDialogOpen(false); router.refresh(); };
  const handleDelete = async (id: string) => { await deleteSoftware(id); setDeleteId(null); router.refresh(); };

  const columns: ColumnDef<SoftwareWithTiers>[] = [
    { accessorKey: 'name', header: 'Name', enableSorting: true },
    {
      accessorKey: 'category', header: 'Category',
      cell: ({ getValue }) => <Badge variant="secondary">{getValue<string>()}</Badge>,
    },
    { accessorKey: 'developer', header: 'Developer' },
    {
      id: 'tiers', header: 'Tiers',
      cell: ({ row }) => {
        const t = row.original.tiers;
        return t.length
          ? <span className="text-xs text-muted-foreground">{t.map((x) => x.name).join(', ')}</span>
          : <span className="text-muted-foreground text-xs">None</span>;
      },
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
          <h1 className="text-2xl font-bold">Software</h1>
          <p className="text-muted-foreground text-sm mt-1">{software.length} total · hardware guidance per usage tier</p>
        </div>
        <Button onClick={() => { setSelected(null); setDialogOpen(true); }}>New Software</Button>
      </div>
      <DataTable columns={columns} data={software} filterPlaceholder="Filter software..." />
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto">
          <DialogHeader><DialogTitle>{selected ? 'Edit Software' : 'New Software'}</DialogTitle></DialogHeader>
          <SoftwareForm item={selected} onSuccess={handleSuccess} />
        </DialogContent>
      </Dialog>
      <AlertDialog open={!!deleteId} onOpenChange={(open) => !open && setDeleteId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Software?</AlertDialogTitle>
            <AlertDialogDescription>
              This cannot be undone. Its tiers and their spec rows are deleted with it.
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
