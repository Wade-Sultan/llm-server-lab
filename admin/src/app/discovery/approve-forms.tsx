'use client';

import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { Checkbox } from '@/components/ui/checkbox';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { centsToUsd, joinCommaList } from '@/lib/utils';
import {
  approveCpu,
  approveGpuChipset,
  approveGpuVariant,
  type ApproveCpuFormData,
  type ApproveGpuChipsetFormData,
  type ApproveGpuVariantFormData,
} from './actions';

export type ChipsetOption = { id: string; name: string };

// extractedFields is untyped Json with snake_case backend keys — these
// coercers keep a malformed extraction from crashing the prefill.
const str = (v: unknown): string => (typeof v === 'string' ? v : '');
const num = (v: unknown): number | null => (typeof v === 'number' ? v : null);
const strArr = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [];

export function cpuDefaults(f: Record<string, unknown>): ApproveCpuFormData {
  return {
    name: str(f.name),
    manufacturer: str(f.manufacturer),
    modelNumber: str(f.model_number),
    yearReleased: num(f.year_released),
    msrpUsd: centsToUsd(num(f.msrp_cents)),
    brand: str(f.brand),
    socket: str(f.socket),
    tdpWatts: num(f.tdp_watts),
    hasIgpu: Boolean(f.has_igpu),
    ddrGenerationInput: joinCommaList(strArr(f.ddr_generation)),
    cores: num(f.cores),
    threads: num(f.threads),
    baseClockGhz: num(f.base_clock_ghz),
    boostClockGhz: num(f.boost_clock_ghz),
    l3CacheMb: num(f.l3_cache_mb),
    pcieGeneration: num(f.pcie_generation),
    maxMemoryGb: num(f.max_memory_gb),
    series: str(f.series),
    supportedFeaturesInput: joinCommaList(strArr(f.supported_features)),
  };
}

export function gpuChipsetDefaults(f: Record<string, unknown>): ApproveGpuChipsetFormData {
  return {
    name: str(f.name),
    vramGb: num(f.vram_gb),
    vramType: str(f.vram_type),
    tdpWatts: num(f.tdp_watts),
    recommendedPsuWatts: num(f.recommended_psu_watts),
    pcieGeneration: num(f.pcie_generation),
    baseClockMhz: num(f.base_clock_mhz),
    boostClockMhz: num(f.boost_clock_mhz),
    hasRayTracing: Boolean(f.has_ray_tracing),
    cudaCores: num(f.cuda_cores),
    tensorCores: num(f.tensor_cores),
    streamProcessors: num(f.stream_processors),
    matrixCores: num(f.matrix_cores),
    supportedFeaturesInput: joinCommaList(strArr(f.supported_features)),
  };
}

export function gpuVariantDefaults(
  f: Record<string, unknown>,
  chipsets: ChipsetOption[],
): ApproveGpuVariantFormData {
  const chipsetName = str(f.chipset_name);
  return {
    name: str(f.name),
    manufacturer: str(f.manufacturer),
    modelNumber: str(f.model_number),
    yearReleased: num(f.year_released),
    msrpUsd: centsToUsd(num(f.msrp_cents)),
    gpuChipsetId:
      chipsets.find((c) => c.name.toLowerCase() === chipsetName.toLowerCase())?.id ?? '',
    brand: str(f.brand),
    lengthMm: num(f.length_mm),
    widthSlots: num(f.width_slots),
    pciePowerPins: str(f.pcie_power_pins),
    displayOutputs: str(f.display_outputs),
    hdmiVersion: str(f.hdmi_version),
    dpVersion: str(f.dp_version),
  };
}

// Required fields mirror the backend subtype NOT NULL columns, so an approve
// can't create a row the catalog schema would reject. No z.coerce here: the
// number inputs already emit number | null, and coercion would silently turn
// a missing required value (null) into 0.

// Nullable in the type (matching the FormData interfaces and the number
// inputs' null-when-empty), non-null at runtime.
const requiredInt = (label: string) =>
  z
    .number()
    .int()
    .nullable()
    .refine((v) => v !== null, { message: `${label} is required` });

const cpuSchema = z.object({
  name: z.string().min(1, 'Name is required'),
  manufacturer: z.string(),
  modelNumber: z.string(),
  yearReleased: z.number().int().nullable(),
  msrpUsd: z.number().nullable(),
  brand: z.string().min(1, 'Brand is required'),
  socket: z.string().min(1, 'Socket is required'),
  tdpWatts: requiredInt('TDP'),
  hasIgpu: z.boolean(),
  ddrGenerationInput: z.string().min(1, 'DDR generation is required'),
  cores: requiredInt('Cores'),
  threads: requiredInt('Threads'),
  baseClockGhz: z.number().nullable(),
  boostClockGhz: z.number().nullable(),
  l3CacheMb: z.number().int().nullable(),
  pcieGeneration: z.number().int().nullable(),
  maxMemoryGb: z.number().int().nullable(),
  series: z.string(),
  supportedFeaturesInput: z.string(),
});

const gpuChipsetSchema = z.object({
  name: z.string().min(1, 'Name is required'),
  vramGb: requiredInt('VRAM'),
  vramType: z.string(),
  tdpWatts: requiredInt('TDP'),
  recommendedPsuWatts: z.number().int().nullable(),
  pcieGeneration: z.number().int().nullable(),
  baseClockMhz: z.number().int().nullable(),
  boostClockMhz: z.number().int().nullable(),
  hasRayTracing: z.boolean(),
  cudaCores: z.number().int().nullable(),
  tensorCores: z.number().int().nullable(),
  streamProcessors: z.number().int().nullable(),
  matrixCores: z.number().int().nullable(),
  supportedFeaturesInput: z.string(),
});

const gpuVariantSchema = z.object({
  name: z.string().min(1, 'Name is required'),
  manufacturer: z.string(),
  modelNumber: z.string(),
  yearReleased: z.number().int().nullable(),
  msrpUsd: z.number().nullable(),
  gpuChipsetId: z.string().min(1, 'Chipset is required'),
  brand: z.string().min(1, 'Brand is required'),
  lengthMm: requiredInt('Length'),
  widthSlots: z.number().nullable(),
  pciePowerPins: z.string(),
  displayOutputs: z.string(),
  hdmiVersion: z.string(),
  dpVersion: z.string(),
});

function TextField({ control, name, label }: { control: any; name: string; label: string }) {
  return (
    <FormField
      control={control}
      name={name}
      render={({ field }) => (
        <FormItem>
          <FormLabel>{label}</FormLabel>
          <FormControl><Input {...field} /></FormControl>
          <FormMessage />
        </FormItem>
      )}
    />
  );
}

function NumberField({
  control,
  name,
  label,
  step,
}: {
  control: any;
  name: string;
  label: string;
  step?: string;
}) {
  return (
    <FormField
      control={control}
      name={name}
      render={({ field }) => (
        <FormItem>
          <FormLabel>{label}</FormLabel>
          <FormControl>
            <Input
              type="number"
              step={step}
              {...field}
              value={field.value ?? ''}
              onChange={(e) =>
                field.onChange(e.target.value === '' ? null : Number(e.target.value))
              }
            />
          </FormControl>
          <FormMessage />
        </FormItem>
      )}
    />
  );
}

function SubmitRow({
  error,
  isSubmitting,
}: {
  error: string | null;
  isSubmitting: boolean;
}) {
  return (
    <>
      {error && <p className="text-sm text-destructive">{error}</p>}
      <div className="flex justify-end pt-2">
        <Button type="submit" disabled={isSubmitting}>
          {isSubmitting ? 'Approving...' : 'Approve & create'}
        </Button>
      </div>
    </>
  );
}

export function ApproveCpuForm({
  itemId,
  extractedFields,
  onSuccess,
}: {
  itemId: string;
  extractedFields: Record<string, unknown>;
  onSuccess: () => void;
}) {
  const form = useForm<ApproveCpuFormData>({
    resolver: zodResolver(cpuSchema),
    defaultValues: cpuDefaults(extractedFields),
  });
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(data: ApproveCpuFormData) {
    setError(null);
    const res = await approveCpu(itemId, data);
    if (res.error) setError(res.error);
    else onSuccess();
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <TextField control={form.control} name="name" label="Name *" />
          <TextField control={form.control} name="manufacturer" label="Manufacturer" />
          <TextField control={form.control} name="modelNumber" label="Model Number" />
          <NumberField control={form.control} name="yearReleased" label="Year Released" />
          <NumberField control={form.control} name="msrpUsd" label="MSRP (USD)" step="0.01" />
          <TextField control={form.control} name="brand" label="Brand * (amd | intel)" />
          <TextField control={form.control} name="socket" label="Socket *" />
          <NumberField control={form.control} name="tdpWatts" label="TDP (Watts) *" />
          <NumberField control={form.control} name="cores" label="Cores *" />
          <NumberField control={form.control} name="threads" label="Threads *" />
          <NumberField control={form.control} name="baseClockGhz" label="Base Clock (GHz)" step="0.1" />
          <NumberField control={form.control} name="boostClockGhz" label="Boost Clock (GHz)" step="0.1" />
          <NumberField control={form.control} name="l3CacheMb" label="L3 Cache (MB)" />
          <NumberField control={form.control} name="pcieGeneration" label="PCIe Generation" />
          <NumberField control={form.control} name="maxMemoryGb" label="Max Memory (GB)" />
          <TextField control={form.control} name="series" label="Series" />
        </div>
        <TextField
          control={form.control}
          name="ddrGenerationInput"
          label="DDR Generation * (comma-separated, e.g. ddr4, ddr5)"
        />
        <TextField
          control={form.control}
          name="supportedFeaturesInput"
          label="Supported Features (comma-separated)"
        />
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
        <SubmitRow error={error} isSubmitting={form.formState.isSubmitting} />
      </form>
    </Form>
  );
}

export function ApproveGpuChipsetForm({
  itemId,
  extractedFields,
  onSuccess,
}: {
  itemId: string;
  extractedFields: Record<string, unknown>;
  onSuccess: () => void;
}) {
  const form = useForm<ApproveGpuChipsetFormData>({
    resolver: zodResolver(gpuChipsetSchema),
    defaultValues: gpuChipsetDefaults(extractedFields),
  });
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(data: ApproveGpuChipsetFormData) {
    setError(null);
    const res = await approveGpuChipset(itemId, data);
    if (res.error) setError(res.error);
    else onSuccess();
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <TextField control={form.control} name="name" label="Chipset Name *" />
          <NumberField control={form.control} name="vramGb" label="VRAM (GB) *" />
          <TextField control={form.control} name="vramType" label="VRAM Type (e.g. gddr7)" />
          <NumberField control={form.control} name="tdpWatts" label="TDP (Watts) *" />
          <NumberField control={form.control} name="recommendedPsuWatts" label="Recommended PSU (W)" />
          <NumberField control={form.control} name="pcieGeneration" label="PCIe Generation" />
          <NumberField control={form.control} name="baseClockMhz" label="Base Clock (MHz)" />
          <NumberField control={form.control} name="boostClockMhz" label="Boost Clock (MHz)" />
          <NumberField control={form.control} name="cudaCores" label="CUDA Cores (Nvidia)" />
          <NumberField control={form.control} name="tensorCores" label="Tensor Cores (Nvidia)" />
          <NumberField control={form.control} name="streamProcessors" label="Stream Processors (AMD)" />
          <NumberField control={form.control} name="matrixCores" label="Matrix Cores (AMD)" />
        </div>
        <TextField
          control={form.control}
          name="supportedFeaturesInput"
          label="Supported Features (comma-separated)"
        />
        <FormField
          control={form.control}
          name="hasRayTracing"
          render={({ field }) => (
            <FormItem className="flex items-center gap-2 space-y-0">
              <FormControl>
                <Checkbox checked={field.value} onCheckedChange={field.onChange} />
              </FormControl>
              <FormLabel>Has Ray Tracing</FormLabel>
            </FormItem>
          )}
        />
        <SubmitRow error={error} isSubmitting={form.formState.isSubmitting} />
      </form>
    </Form>
  );
}

export function ApproveGpuVariantForm({
  itemId,
  extractedFields,
  chipsets,
  onSuccess,
}: {
  itemId: string;
  extractedFields: Record<string, unknown>;
  chipsets: ChipsetOption[];
  onSuccess: () => void;
}) {
  const form = useForm<ApproveGpuVariantFormData>({
    resolver: zodResolver(gpuVariantSchema),
    defaultValues: gpuVariantDefaults(extractedFields, chipsets),
  });
  const [error, setError] = useState<string | null>(null);
  const extractedChipsetName = str(extractedFields.chipset_name);

  async function onSubmit(data: ApproveGpuVariantFormData) {
    setError(null);
    const res = await approveGpuVariant(itemId, data);
    if (res.error) setError(res.error);
    else onSuccess();
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <TextField control={form.control} name="name" label="Name *" />
          <TextField control={form.control} name="manufacturer" label="Manufacturer (board partner)" />
          <TextField control={form.control} name="modelNumber" label="Model Number" />
          <NumberField control={form.control} name="yearReleased" label="Year Released" />
          <NumberField control={form.control} name="msrpUsd" label="MSRP (USD)" step="0.01" />
          <FormField
            control={form.control}
            name="gpuChipsetId"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Chipset *</FormLabel>
                <Select value={field.value} onValueChange={field.onChange}>
                  <FormControl>
                    <SelectTrigger>
                      <SelectValue placeholder="Select a chipset" />
                    </SelectTrigger>
                  </FormControl>
                  <SelectContent>
                    {chipsets.map((c) => (
                      <SelectItem key={c.id} value={c.id}>
                        {c.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {!field.value && extractedChipsetName && (
                  <p className="text-xs text-muted-foreground">
                    Extracted: &quot;{extractedChipsetName}&quot; — no matching chipset in the
                    catalog; approve the chipset first.
                  </p>
                )}
                <FormMessage />
              </FormItem>
            )}
          />
          <TextField control={form.control} name="brand" label="Brand * (nvidia | amd | intel)" />
          <NumberField control={form.control} name="lengthMm" label="Length (mm) *" />
          <NumberField control={form.control} name="widthSlots" label="Width (slots)" step="0.1" />
          <TextField control={form.control} name="pciePowerPins" label="PCIe Power Pins" />
          <TextField control={form.control} name="displayOutputs" label="Display Outputs" />
          <TextField control={form.control} name="hdmiVersion" label="HDMI Version" />
          <TextField control={form.control} name="dpVersion" label="DP Version" />
        </div>
        <SubmitRow error={error} isSubmitting={form.formState.isSubmitting} />
      </form>
    </Form>
  );
}
