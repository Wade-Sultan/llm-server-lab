'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { useFieldArray, useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import type { ColumnDef } from '@tanstack/react-table';
import type { Game, GameMinimumPart } from '@prisma/client';
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { joinCommaList } from '@/lib/utils';
import { createGame, updateGame, deleteGame, type GameFormData } from './actions';

const TIERS = ['minimum', 'recommended', 'ultra'] as const;
const ROLES = ['cpu', 'gpu'] as const;

type PartOption = { id: string; name: string; partType: string };
type GameWithParts = Game & {
  minimumParts: (GameMinimumPart & { part: { id: string; name: string } | null })[];
};

const minimumPartSchema = z.object({
  tier: z.string().min(1),
  role: z.string().min(1),
  partId: z.string().nullable(),
  publishedName: z.string(),
  minRamGb: z.coerce.number().int().nullable(),
});

const schema = z.object({
  title: z.string().min(1, 'Title is required'),
  slug: z.string(),
  genre: z.string(),
  storeUrl: z.string(),
  imageUrl: z.string(),
  hardRequirementsInput: z.string(),
  minStorageGb: z.coerce.number().int().nullable(),
  requirementsNotes: z.string(),
  minimumParts: z.array(minimumPartSchema),
});

function gameDefaults(item: GameWithParts | null): GameFormData {
  if (!item) {
    return {
      title: '', slug: '', genre: '', storeUrl: '', imageUrl: '',
      hardRequirementsInput: '', minStorageGb: null, requirementsNotes: '',
      minimumParts: [],
    };
  }
  return {
    title: item.title,
    slug: item.slug,
    genre: item.genre ?? '',
    storeUrl: item.storeUrl ?? '',
    imageUrl: item.imageUrl ?? '',
    hardRequirementsInput: joinCommaList(item.hardRequirements),
    minStorageGb: item.minStorageGb,
    requirementsNotes: item.requirementsNotes ?? '',
    minimumParts: item.minimumParts.map((p) => ({
      tier: p.tier,
      role: p.role,
      partId: p.partId,
      publishedName: p.publishedName ?? '',
      minRamGb: p.minRamGb,
    })),
  };
}

function GameForm({
  item, partOptions, onSuccess,
}: {
  item: GameWithParts | null;
  partOptions: PartOption[];
  onSuccess: () => void;
}) {
  const form = useForm<GameFormData>({
    resolver: zodResolver(schema),
    defaultValues: gameDefaults(item),
  });
  const rows = useFieldArray({ control: form.control, name: 'minimumParts' });
  const [error, setError] = useState<string | null>(null);

  const numField = (field: { value: number | null; onChange: (v: number | null) => void }) => ({
    ...field,
    type: 'number' as const,
    value: field.value ?? '',
    onChange: (e: React.ChangeEvent<HTMLInputElement>) =>
      field.onChange(e.target.value === '' ? null : Number(e.target.value)),
  });

  async function onSubmit(data: GameFormData) {
    setError(null);
    try {
      if (item) { await updateGame(item.id, data); } else { await createGame(data); }
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
            ['title', 'Title *'], ['slug', 'Slug (blank = from title)'], ['genre', 'Genre (e.g. "competitive_fps")'],
            ['storeUrl', 'Store URL'], ['imageUrl', 'Image URL'],
          ] as [keyof GameFormData, string][]).map(([name, label]) => (
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
          <FormField control={form.control} name="minStorageGb"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Min Storage (GB)</FormLabel>
                <FormControl>
                  <Input {...numField(field as { value: number | null; onChange: (v: number | null) => void })} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
        </div>
        <FormField control={form.control} name="hardRequirementsInput"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Hard Requirements (comma-separated)</FormLabel>
              <FormControl><Input {...field} placeholder="e.g. avx2, ray_tracing" /></FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField control={form.control} name="requirementsNotes"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Requirements Notes</FormLabel>
              <FormControl><Textarea rows={2} {...field} /></FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <div className="border-t pt-4 space-y-3">
          <div className="flex items-center justify-between">
            <p className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
              Minimum Parts (one per tier × role)
            </p>
            <Button type="button" variant="outline" size="sm"
              onClick={() => rows.append({ tier: 'minimum', role: 'cpu', partId: null, publishedName: '', minRamGb: null })}>
              <Plus className="h-3.5 w-3.5" /> Add Row
            </Button>
          </div>
          {rows.fields.map((row, i) => (
            <div key={row.id} className="grid grid-cols-[1fr_1fr_2fr_2fr_1fr_auto] gap-2 items-end border rounded-md p-2">
              <FormField control={form.control} name={`minimumParts.${i}.tier`}
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-xs">Tier</FormLabel>
                    <Select value={field.value} onValueChange={field.onChange}>
                      <FormControl><SelectTrigger><SelectValue /></SelectTrigger></FormControl>
                      <SelectContent>
                        {TIERS.map((t) => <SelectItem key={t} value={t}>{t}</SelectItem>)}
                      </SelectContent>
                    </Select>
                  </FormItem>
                )}
              />
              <FormField control={form.control} name={`minimumParts.${i}.role`}
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-xs">Role</FormLabel>
                    <Select value={field.value}
                      onValueChange={(v) => { field.onChange(v); form.setValue(`minimumParts.${i}.partId`, null); }}>
                      <FormControl><SelectTrigger><SelectValue /></SelectTrigger></FormControl>
                      <SelectContent>
                        {ROLES.map((r) => <SelectItem key={r} value={r}>{r.toUpperCase()}</SelectItem>)}
                      </SelectContent>
                    </Select>
                  </FormItem>
                )}
              />
              <FormField control={form.control} name={`minimumParts.${i}.partId`}
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-xs">Catalog Part</FormLabel>
                    <Select value={field.value ?? '__none'}
                      onValueChange={(v) => field.onChange(v === '__none' ? null : v)}>
                      <FormControl><SelectTrigger><SelectValue placeholder="— None —" /></SelectTrigger></FormControl>
                      <SelectContent>
                        <SelectItem value="__none">— None —</SelectItem>
                        {partOptions
                          .filter((p) => p.partType === form.watch(`minimumParts.${i}.role`))
                          .map((p) => <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>)}
                      </SelectContent>
                    </Select>
                  </FormItem>
                )}
              />
              <FormField control={form.control} name={`minimumParts.${i}.publishedName`}
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-xs">Published Name</FormLabel>
                    <FormControl><Input {...field} placeholder="e.g. GTX 1060 6GB" /></FormControl>
                  </FormItem>
                )}
              />
              <FormField control={form.control} name={`minimumParts.${i}.minRamGb`}
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-xs">RAM (GB)</FormLabel>
                    <FormControl>
                      <Input {...numField(field as { value: number | null; onChange: (v: number | null) => void })} />
                    </FormControl>
                  </FormItem>
                )}
              />
              <Button type="button" variant="ghost" size="icon" onClick={() => rows.remove(i)}>
                <X className="h-3.5 w-3.5" />
              </Button>
            </div>
          ))}
        </div>

        {error && <p className="text-sm text-destructive">{error}</p>}
        <div className="flex justify-end pt-2">
          <Button type="submit" disabled={form.formState.isSubmitting}>
            {form.formState.isSubmitting ? 'Saving...' : item ? 'Update Game' : 'Create Game'}
          </Button>
        </div>
      </form>
    </Form>
  );
}

export function GamesTable({ games, partOptions }: { games: GameWithParts[]; partOptions: PartOption[] }) {
  const router = useRouter();
  const [selected, setSelected] = useState<GameWithParts | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deleteId, setDeleteId] = useState<string | null>(null);

  const handleSuccess = () => { setDialogOpen(false); router.refresh(); };
  const handleDelete = async (id: string) => { await deleteGame(id); setDeleteId(null); router.refresh(); };

  const columns: ColumnDef<GameWithParts>[] = [
    { accessorKey: 'title', header: 'Title', enableSorting: true },
    {
      accessorKey: 'genre', header: 'Genre',
      cell: ({ getValue }) => getValue<string | null>()
        ? <Badge variant="secondary">{getValue<string>()}</Badge>
        : <span className="text-muted-foreground text-xs">—</span>,
    },
    { accessorKey: 'minStorageGb', header: 'Storage (GB)', enableSorting: true },
    {
      id: 'specs', header: 'Spec Rows',
      cell: ({ row }) => {
        const n = row.original.minimumParts.length;
        return n
          ? <span className="text-xs text-muted-foreground">{n} tier/role rows</span>
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
          <h1 className="text-2xl font-bold">Games</h1>
          <p className="text-muted-foreground text-sm mt-1">{games.length} total · published spec requirements per tier</p>
        </div>
        <Button onClick={() => { setSelected(null); setDialogOpen(true); }}>New Game</Button>
      </div>
      <DataTable columns={columns} data={games} filterPlaceholder="Filter games..." filterColumn="title" />
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto">
          <DialogHeader><DialogTitle>{selected ? 'Edit Game' : 'New Game'}</DialogTitle></DialogHeader>
          <GameForm item={selected} partOptions={partOptions} onSuccess={handleSuccess} />
        </DialogContent>
      </Dialog>
      <AlertDialog open={!!deleteId} onOpenChange={(open) => !open && setDeleteId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Game?</AlertDialogTitle>
            <AlertDialogDescription>
              This cannot be undone. Its minimum-spec rows are deleted with it.
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
