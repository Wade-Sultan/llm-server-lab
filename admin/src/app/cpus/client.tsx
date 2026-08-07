'use client';

import { useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import type { ColumnDef } from '@tanstack/react-table';
import type { Cpu, PcPart, Listing, AmazonListing } from '@prisma/client';
import { Pencil, Trash2 } from 'lucide-react';
import { DataTable } from '@/components/data-table';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Checkbox } from '@/components/ui/checkbox';
import { ListingsDialog } from '@/components/listings-dialog';
import { joinCommaList, centsToUsd, formatUsd } from '@/lib/utils';
import { createCpu, updateCpu, deleteCpu, type CpuFormData } from './actions';

type CpuWithPart = Cpu & { pcPart: PcPart & { listings: (Listing & { amazonListing: AmazonListing | null })[] } };

const schema = z.object({
  name: z.string().min(1, 'Name is required'),
  manufacturer: z.string(),
  modelNumber: z.string(),
  yearReleased: z.coerce.number().int().nullable(),
  isActive: z.boolean(),
  streetPriceUsd: z.coerce.number().nullable(),
  brand: z.string(),
  socket: z.string(),
  tdpWatts: z.coerce.number().int().nullable(),
  hasIgpu: z.boolean(),
  ddrGenerationInput: z.string(),
  cores: z.coerce.number().int().nullable(),
  threads: z.coerce.number().int().nullable(),
  baseClockGhz: z.coerce.number().nullable(),
  boostClockGhz: z.coerce.number().nullable(),
  l3CacheMb: z.coerce.number().int().nullable(),
  pcieGeneration: z.coerce.number().int().nullable(),
  maxMemoryGb: z.coerce.number().int().nullable(),
  series: z.string(),
  supportedFeaturesInput: z.string(),
  benchmarkScoresInput: z.string(),
});

function CpuForm({
  item,
  onSuccess,
}: {
  item: CpuWithPart | null;
  onSuccess: () => void;
}) {
  const form = useForm<CpuFormData>({
    resolver: zodResolver(schema),
    defaultValues: item
      ? {
          name: item.pcPart.name,
          manufacturer: item.pcPart.manufacturer ?? '',
          modelNumber: item.pcPart.modelNumber ?? '',
          yearReleased: item.pcPart.yearReleased,
          isActive: item.pcPart.isActive,
          streetPriceUsd: centsToUsd(item.pcPart.streetPriceCents),
          brand: item.brand ?? '',
          socket: item.socket ?? '',
          tdpWatts: item.tdpWatts,
          hasIgpu: item.hasIgpu,
          ddrGenerationInput: joinCommaList(item.ddrGeneration),
          cores: item.cores,
          threads: item.threads,
          baseClockGhz: item.baseClockGhz,
          boostClockGhz: item.boostClockGhz,
          l3CacheMb: item.l3CacheMb,
          pcieGeneration: item.pcieGeneration,
          maxMemoryGb: item.maxMemoryGb,
          series: item.series ?? '',
          supportedFeaturesInput: joinCommaList(item.supportedFeatures),
          benchmarkScoresInput: item.benchmarkScores
            ? JSON.stringify(item.benchmarkScores, null, 2)
            : '',
        }
      : {
          name: '',
          manufacturer: '',
          modelNumber: '',
          yearReleased: null,
          isActive: true,
          streetPriceUsd: null,
          brand: '',
          socket: '',
          tdpWatts: null,
          hasIgpu: false,
          ddrGenerationInput: '',
          cores: null,
          threads: null,
          baseClockGhz: null,
          boostClockGhz: null,
          l3CacheMb: null,
          pcieGeneration: null,
          maxMemoryGb: null,
          series: '',
          supportedFeaturesInput: '',
          benchmarkScoresInput: '',
        },
  });

  const [error, setError] = useState<string | null>(null);

  async function onSubmit(data: CpuFormData) {
    setError(null);
    try {
      if (item) {
        await updateCpu(item.id, data);
      } else {
        await createCpu(data);
      }
      onSuccess();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'An error occurred');
    }
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <FormField
            control={form.control}
            name="name"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Name *</FormLabel>
                <FormControl><Input {...field} /></FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="manufacturer"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Manufacturer</FormLabel>
                <FormControl><Input {...field} /></FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="modelNumber"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Model Number</FormLabel>
                <FormControl><Input {...field} /></FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="yearReleased"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Year Released</FormLabel>
                <FormControl>
                  <Input type="number" {...field} value={field.value ?? ''} onChange={e => field.onChange(e.target.value === '' ? null : Number(e.target.value))} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="streetPriceUsd"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Street Price (USD)</FormLabel>
                <FormControl>
                  <Input type="number" step="0.01" {...field} value={field.value ?? ''} onChange={e => field.onChange(e.target.value === '' ? null : Number(e.target.value))} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="brand"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Brand</FormLabel>
                <FormControl><Input {...field} /></FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="socket"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Socket</FormLabel>
                <FormControl><Input {...field} /></FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="tdpWatts"
            render={({ field }) => (
              <FormItem>
                <FormLabel>TDP (Watts)</FormLabel>
                <FormControl>
                  <Input type="number" {...field} value={field.value ?? ''} onChange={e => field.onChange(e.target.value === '' ? null : Number(e.target.value))} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="cores"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Cores</FormLabel>
                <FormControl>
                  <Input type="number" {...field} value={field.value ?? ''} onChange={e => field.onChange(e.target.value === '' ? null : Number(e.target.value))} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="threads"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Threads</FormLabel>
                <FormControl>
                  <Input type="number" {...field} value={field.value ?? ''} onChange={e => field.onChange(e.target.value === '' ? null : Number(e.target.value))} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="baseClockGhz"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Base Clock (GHz)</FormLabel>
                <FormControl>
                  <Input type="number" step="0.1" {...field} value={field.value ?? ''} onChange={e => field.onChange(e.target.value === '' ? null : Number(e.target.value))} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="boostClockGhz"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Boost Clock (GHz)</FormLabel>
                <FormControl>
                  <Input type="number" step="0.1" {...field} value={field.value ?? ''} onChange={e => field.onChange(e.target.value === '' ? null : Number(e.target.value))} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="l3CacheMb"
            render={({ field }) => (
              <FormItem>
                <FormLabel>L3 Cache (MB)</FormLabel>
                <FormControl>
                  <Input type="number" {...field} value={field.value ?? ''} onChange={e => field.onChange(e.target.value === '' ? null : Number(e.target.value))} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="pcieGeneration"
            render={({ field }) => (
              <FormItem>
                <FormLabel>PCIe Generation</FormLabel>
                <FormControl>
                  <Input type="number" {...field} value={field.value ?? ''} onChange={e => field.onChange(e.target.value === '' ? null : Number(e.target.value))} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="maxMemoryGb"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Max Memory (GB)</FormLabel>
                <FormControl>
                  <Input type="number" {...field} value={field.value ?? ''} onChange={e => field.onChange(e.target.value === '' ? null : Number(e.target.value))} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="series"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Series</FormLabel>
                <FormControl><Input {...field} /></FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
        </div>
        <FormField
          control={form.control}
          name="ddrGenerationInput"
          render={({ field }) => (
            <FormItem>
              {/* Mention ddr6 in the label when DDR6 parts exist. */}
              <FormLabel>DDR Generation (comma-separated, e.g. ddr4, ddr5)</FormLabel>
              <FormControl><Input {...field} /></FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="supportedFeaturesInput"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Supported Features (comma-separated)</FormLabel>
              <FormControl><Input {...field} /></FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="benchmarkScoresInput"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Benchmark Scores (JSON)</FormLabel>
              <FormControl><Textarea rows={4} {...field} /></FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <div className="flex gap-6">
          <FormField
            control={form.control}
            name="hasIgpu"
            render={({ field }) => (
              <FormItem className="flex items-center gap-2 space-y-0">
                <FormControl>
                  <Checkbox checked={field.value} onCheckedChange={field.onChange} />
                </FormControl>
                <FormLabel>Has iGPU</FormLabel>
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="isActive"
            render={({ field }) => (
              <FormItem className="flex items-center gap-2 space-y-0">
                <FormControl>
                  <Checkbox checked={field.value} onCheckedChange={field.onChange} />
                </FormControl>
                <FormLabel>Active</FormLabel>
              </FormItem>
            )}
          />
        </div>
        {error && <p className="text-sm text-destructive">{error}</p>}
        <div className="flex justify-end gap-2 pt-2">
          <Button type="submit" disabled={form.formState.isSubmitting}>
            {form.formState.isSubmitting ? 'Saving...' : item ? 'Update CPU' : 'Create CPU'}
          </Button>
        </div>
      </form>
    </Form>
  );
}

export function CpuTable({ data }: { data: CpuWithPart[] }) {
  const router = useRouter();
  const [selected, setSelected] = useState<CpuWithPart | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const [, startTransition] = useTransition();

  const handleSuccess = () => {
    setDialogOpen(false);
    router.refresh();
  };

  const handleDelete = (id: string) => {
    startTransition(async () => {
      await deleteCpu(id);
      setDeleteId(null);
      router.refresh();
    });
  };

  const columns: ColumnDef<CpuWithPart>[] = [
    {
      id: 'name',
      accessorFn: (row) => row.pcPart.name,
      header: 'Name',
      enableSorting: true,
    },
    {
      id: 'manufacturer',
      accessorFn: (row) => row.pcPart.manufacturer ?? '',
      header: 'Manufacturer',
      enableSorting: true,
    },
    { accessorKey: 'socket', header: 'Socket', enableSorting: true },
    { accessorKey: 'cores', header: 'Cores', enableSorting: true },
    { accessorKey: 'tdpWatts', header: 'TDP (W)', enableSorting: true },
    {
      id: 'streetPrice',
      accessorFn: (row) => row.pcPart.streetPriceCents,
      header: 'Street Price',
      cell: ({ getValue }) => formatUsd(getValue<number | null>()),
      enableSorting: true,
    },
    {
      id: 'listings', header: 'Listings',
      cell: ({ row }) => (
        <ListingsDialog
          partId={row.original.pcPart.id}
          partName={row.original.pcPart.name}
          listings={row.original.pcPart.listings}
        />
      ),
    },
    {
      id: 'isActive',
      accessorFn: (row) => row.pcPart.isActive,
      header: 'Active',
      cell: ({ getValue }) => (
        <Badge variant={getValue<boolean>() ? 'default' : 'secondary'}>
          {getValue<boolean>() ? 'Active' : 'Inactive'}
        </Badge>
      ),
    },
    {
      id: 'actions',
      header: '',
      cell: ({ row }) => (
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => { setSelected(row.original); setDialogOpen(true); }}
          >
            <Pencil className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="text-destructive hover:text-destructive"
            onClick={() => setDeleteId(row.original.id)}
          >
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
          <h1 className="text-2xl font-bold">CPUs</h1>
          <p className="text-muted-foreground text-sm mt-1">{data.length} total</p>
        </div>
        <Button onClick={() => { setSelected(null); setDialogOpen(true); }}>New CPU</Button>
      </div>
      <DataTable columns={columns} data={data} filterPlaceholder="Filter CPUs..." />

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>{selected ? 'Edit CPU' : 'New CPU'}</DialogTitle>
          </DialogHeader>
          <CpuForm item={selected} onSuccess={handleSuccess} />
        </DialogContent>
      </Dialog>

      <AlertDialog open={!!deleteId} onOpenChange={(open) => !open && setDeleteId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete CPU?</AlertDialogTitle>
            <AlertDialogDescription>This action cannot be undone.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => deleteId && handleDelete(deleteId)}>
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
