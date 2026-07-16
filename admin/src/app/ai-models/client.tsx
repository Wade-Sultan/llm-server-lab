'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { useFieldArray, useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import type { ColumnDef } from '@tanstack/react-table';
import type { AiModel, AiWorkload } from '@prisma/client';
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
import { createAiModel, updateAiModel, deleteAiModel, type AiModelFormData } from './actions';

const FAMILIES = [
  'llm', 'multimodal', 'image_gen', 'video_gen', 'speech',
  'audio_gen', 'vision', 'embedding', 'classical', 'rl',
] as const;
const TASKS = [
  'inference', 'fine_tune_full', 'fine_tune_lora', 'fine_tune_qlora',
  'post_train', 'train_scratch',
] as const;
const GPU_IMPORTANCE = ['required', 'accelerated', 'optional', 'irrelevant'] as const;

type ModelWithWorkloads = AiModel & { workloads: AiWorkload[] };

const jsonOrEmpty = (label: string) =>
  z.string().refine((v) => {
    if (!v) return true;
    try { JSON.parse(v); return true; } catch { return false; }
  }, { message: `${label} must be valid JSON` });

const workloadSchema = z.object({
  id: z.string().nullable(),
  task: z.string().min(1),
  precision: z.string().min(1, 'Precision required'),
  gpuImportance: z.string().min(1),
  minVramGb: z.coerce.number().int().nullable(),
  recommendedVramGb: z.coerce.number().int().nullable(),
  supportsMultiGpu: z.boolean(),
  cpuOffloadCapable: z.boolean(),
  minRamGb: z.coerce.number().int().nullable(),
  recommendedRamGb: z.coerce.number().int().nullable(),
  minStorageGb: z.coerce.number().int().nullable(),
  gpuBackendsInput: z.string(),
  requiredGpuFeaturesInput: z.string(),
  assumptionsInput: jsonOrEmpty('Assumptions'),
  notes: z.string(),
});

const schema = z.object({
  name: z.string().min(1, 'Name is required'),
  slug: z.string(),
  family: z.string().min(1, 'Family is required'),
  paramsBillions: z.coerce.number().nullable(),
  contextLength: z.coerce.number().int().nullable(),
  developer: z.string(),
  license: z.string(),
  huggingfaceId: z.string(),
  websiteUrl: z.string(),
  imageUrl: z.string(),
  specInput: jsonOrEmpty('Spec'),
  notes: z.string(),
  workloads: z.array(workloadSchema),
});

function modelDefaults(item: ModelWithWorkloads | null): AiModelFormData {
  if (!item) {
    return {
      name: '', slug: '', family: 'llm', paramsBillions: null, contextLength: null,
      developer: '', license: '', huggingfaceId: '', websiteUrl: '', imageUrl: '',
      specInput: '', notes: '', workloads: [],
    };
  }
  return {
    name: item.name,
    slug: item.slug,
    family: item.family,
    paramsBillions: item.paramsBillions,
    contextLength: item.contextLength,
    developer: item.developer ?? '',
    license: item.license ?? '',
    huggingfaceId: item.huggingfaceId ?? '',
    websiteUrl: item.websiteUrl ?? '',
    imageUrl: item.imageUrl ?? '',
    specInput: item.spec ? JSON.stringify(item.spec, null, 2) : '',
    notes: item.notes ?? '',
    workloads: item.workloads.map((w) => ({
      id: w.id,
      task: w.task,
      precision: w.precision,
      gpuImportance: w.gpuImportance,
      minVramGb: w.minVramGb,
      recommendedVramGb: w.recommendedVramGb,
      supportsMultiGpu: w.supportsMultiGpu,
      cpuOffloadCapable: w.cpuOffloadCapable,
      minRamGb: w.minRamGb,
      recommendedRamGb: w.recommendedRamGb,
      minStorageGb: w.minStorageGb,
      gpuBackendsInput: joinCommaList(w.gpuBackends),
      requiredGpuFeaturesInput: joinCommaList(w.requiredGpuFeatures),
      assumptionsInput: w.assumptions ? JSON.stringify(w.assumptions, null, 2) : '',
      notes: w.notes ?? '',
    })),
  };
}

function AiModelForm({ item, onSuccess }: { item: ModelWithWorkloads | null; onSuccess: () => void }) {
  const form = useForm<AiModelFormData>({
    resolver: zodResolver(schema),
    defaultValues: modelDefaults(item),
  });
  const workloads = useFieldArray({ control: form.control, name: 'workloads' });
  const [error, setError] = useState<string | null>(null);

  const numField = (field: { value: number | null; onChange: (v: number | null) => void }, step?: string) => ({
    ...field,
    type: 'number' as const,
    step,
    value: field.value ?? '',
    onChange: (e: React.ChangeEvent<HTMLInputElement>) =>
      field.onChange(e.target.value === '' ? null : Number(e.target.value)),
  });

  async function onSubmit(data: AiModelFormData) {
    setError(null);
    try {
      if (item) { await updateAiModel(item.id, data); } else { await createAiModel(data); }
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
            ['name', 'Name * (e.g. "Llama 3.1 70B")'], ['slug', 'Slug (blank = from name)'],
            ['developer', 'Developer'], ['license', 'License'],
            ['huggingfaceId', 'HuggingFace ID'], ['websiteUrl', 'Website URL'], ['imageUrl', 'Image URL'],
          ] as [keyof AiModelFormData, string][]).map(([name, label]) => (
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
          <FormField control={form.control} name="family"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Family *</FormLabel>
                <Select value={field.value} onValueChange={field.onChange}>
                  <FormControl><SelectTrigger><SelectValue /></SelectTrigger></FormControl>
                  <SelectContent>
                    {FAMILIES.map((f) => <SelectItem key={f} value={f}>{f}</SelectItem>)}
                  </SelectContent>
                </Select>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField control={form.control} name="paramsBillions"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Params (billions)</FormLabel>
                <FormControl>
                  <Input {...numField(field as { value: number | null; onChange: (v: number | null) => void }, '0.001')} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField control={form.control} name="contextLength"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Context Length (tokens)</FormLabel>
                <FormControl>
                  <Input {...numField(field as { value: number | null; onChange: (v: number | null) => void })} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
        </div>
        <FormField control={form.control} name="specInput"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Family Spec (JSON, e.g. {'{"base_resolution": 1024}'})</FormLabel>
              <FormControl><Textarea rows={2} {...field} /></FormControl>
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

        <div className="border-t pt-4 space-y-3">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">Workloads</p>
              <p className="text-xs text-muted-foreground">
                One row per task × precision. VRAM is total across GPUs.
              </p>
            </div>
            <Button type="button" variant="outline" size="sm"
              onClick={() => workloads.append({
                id: null, task: 'inference', precision: '', gpuImportance: 'required',
                minVramGb: null, recommendedVramGb: null, supportsMultiGpu: false,
                cpuOffloadCapable: false, minRamGb: null, recommendedRamGb: null,
                minStorageGb: null, gpuBackendsInput: 'cuda', requiredGpuFeaturesInput: '',
                assumptionsInput: '', notes: '',
              })}>
              <Plus className="h-3.5 w-3.5" /> Add Workload
            </Button>
          </div>
          {workloads.fields.map((row, i) => (
            <div key={row.id} className="border rounded-md p-3 space-y-2">
              <div className="grid grid-cols-[2fr_1fr_1fr_auto] gap-2 items-end">
                <FormField control={form.control} name={`workloads.${i}.task`}
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-xs">Task</FormLabel>
                      <Select value={field.value} onValueChange={field.onChange}>
                        <FormControl><SelectTrigger><SelectValue /></SelectTrigger></FormControl>
                        <SelectContent>
                          {TASKS.map((t) => <SelectItem key={t} value={t}>{t}</SelectItem>)}
                        </SelectContent>
                      </Select>
                    </FormItem>
                  )}
                />
                <FormField control={form.control} name={`workloads.${i}.precision`}
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-xs">Precision *</FormLabel>
                      <FormControl><Input {...field} placeholder="fp16 / q4 / fp8…" /></FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField control={form.control} name={`workloads.${i}.gpuImportance`}
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
                <Button type="button" variant="ghost" size="icon" onClick={() => workloads.remove(i)}>
                  <X className="h-3.5 w-3.5" />
                </Button>
              </div>
              <div className="grid grid-cols-5 gap-2">
                {([
                  ['minVramGb', 'Min VRAM'], ['recommendedVramGb', 'Rec. VRAM'],
                  ['minRamGb', 'Min RAM'], ['recommendedRamGb', 'Rec. RAM'], ['minStorageGb', 'Storage'],
                ] as const).map(([name, label]) => (
                  <FormField key={name} control={form.control} name={`workloads.${i}.${name}`}
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
              <div className="grid grid-cols-2 gap-2">
                <FormField control={form.control} name={`workloads.${i}.gpuBackendsInput`}
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-xs">GPU Backends (comma-separated)</FormLabel>
                      <FormControl><Input {...field} placeholder="cuda, rocm, metal" /></FormControl>
                    </FormItem>
                  )}
                />
                <FormField control={form.control} name={`workloads.${i}.requiredGpuFeaturesInput`}
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-xs">Required GPU Features (comma-separated)</FormLabel>
                      <FormControl><Input {...field} placeholder="bf16, fp8" /></FormControl>
                    </FormItem>
                  )}
                />
              </div>
              <div className="flex items-center gap-6">
                <FormField control={form.control} name={`workloads.${i}.supportsMultiGpu`}
                  render={({ field }) => (
                    <FormItem className="flex items-center gap-2 space-y-0">
                      <FormControl><Checkbox checked={field.value} onCheckedChange={field.onChange} /></FormControl>
                      <FormLabel className="text-xs">Multi-GPU capable</FormLabel>
                    </FormItem>
                  )}
                />
                <FormField control={form.control} name={`workloads.${i}.cpuOffloadCapable`}
                  render={({ field }) => (
                    <FormItem className="flex items-center gap-2 space-y-0">
                      <FormControl><Checkbox checked={field.value} onCheckedChange={field.onChange} /></FormControl>
                      <FormLabel className="text-xs">CPU offload capable</FormLabel>
                    </FormItem>
                  )}
                />
              </div>
              <div className="grid grid-cols-2 gap-2">
                <FormField control={form.control} name={`workloads.${i}.assumptionsInput`}
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-xs">Assumptions (JSON)</FormLabel>
                      <FormControl><Textarea rows={2} {...field} placeholder='{"context_tokens": 8192}' /></FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField control={form.control} name={`workloads.${i}.notes`}
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-xs">Notes</FormLabel>
                      <FormControl><Textarea rows={2} {...field} /></FormControl>
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
            {form.formState.isSubmitting ? 'Saving...' : item ? 'Update Model' : 'Create Model'}
          </Button>
        </div>
      </form>
    </Form>
  );
}

export function AiModelsTable({ models }: { models: ModelWithWorkloads[] }) {
  const router = useRouter();
  const [selected, setSelected] = useState<ModelWithWorkloads | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deleteId, setDeleteId] = useState<string | null>(null);

  const handleSuccess = () => { setDialogOpen(false); router.refresh(); };
  const handleDelete = async (id: string) => { await deleteAiModel(id); setDeleteId(null); router.refresh(); };

  const columns: ColumnDef<ModelWithWorkloads>[] = [
    { accessorKey: 'name', header: 'Name', enableSorting: true },
    {
      accessorKey: 'family', header: 'Family',
      cell: ({ getValue }) => <Badge variant="secondary">{getValue<string>()}</Badge>,
    },
    {
      accessorKey: 'paramsBillions', header: 'Params (B)', enableSorting: true,
      cell: ({ getValue }) => getValue<number | null>() ?? <span className="text-muted-foreground text-xs">—</span>,
    },
    {
      id: 'workloads', header: 'Workloads',
      cell: ({ row }) => {
        const w = row.original.workloads;
        return w.length
          ? <span className="text-xs text-muted-foreground">{w.map((x) => `${x.task}@${x.precision}`).join(', ')}</span>
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
          <h1 className="text-2xl font-bold">AI Models</h1>
          <p className="text-muted-foreground text-sm mt-1">
            {models.length} total · hardware requirements per model × task × precision
          </p>
        </div>
        <Button onClick={() => { setSelected(null); setDialogOpen(true); }}>New Model</Button>
      </div>
      <DataTable columns={columns} data={models} filterPlaceholder="Filter models..." />
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto">
          <DialogHeader><DialogTitle>{selected ? 'Edit AI Model' : 'New AI Model'}</DialogTitle></DialogHeader>
          <AiModelForm item={selected} onSuccess={handleSuccess} />
        </DialogContent>
      </Dialog>
      <AlertDialog open={!!deleteId} onOpenChange={(open) => !open && setDeleteId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete AI Model?</AlertDialogTitle>
            <AlertDialogDescription>
              This cannot be undone. Its workload rows are deleted with it.
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
