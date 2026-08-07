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
import { centsToUsd, joinCommaList, slugify } from '@/lib/utils';
import {
  approveAiModel,
  approveCase,
  approveCpu,
  approveCpuCooler,
  approveFan,
  approveGpuChipset,
  approveGpuVariant,
  approveMotherboard,
  approvePsu,
  approveRamKit,
  approveStorageDrive,
  type ApproveAiModelFormData,
  type ApproveCaseFormData,
  type ApproveCpuCoolerFormData,
  type ApproveCpuFormData,
  type ApproveFanFormData,
  type ApproveGpuChipsetFormData,
  type ApproveGpuVariantFormData,
  type ApproveMotherboardFormData,
  type ApprovePsuFormData,
  type ApproveRamKitFormData,
  type ApproveStorageDriveFormData,
} from './actions';

export type ChipsetOption = { id: string; name: string };
/** Existing *_groups rows the reviewer can attach a RAM/storage/PSU SKU to
 * instead of minting a duplicate group. Empty selection = create a new one. */
export type GroupOption = { id: string; name: string };

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

// Fields every pc_parts-backed approval shares. Split out because eight
// categories repeat them verbatim.
const partDefaults = (f: Record<string, unknown>) => ({
  name: str(f.name),
  manufacturer: str(f.manufacturer),
  modelNumber: str(f.model_number),
  yearReleased: num(f.year_released),
  msrpUsd: centsToUsd(num(f.msrp_cents)),
});

export function motherboardDefaults(
  f: Record<string, unknown>,
): ApproveMotherboardFormData {
  return {
    ...partDefaults(f),
    socket: str(f.socket),
    formFactor: str(f.form_factor),
    ddrGeneration: str(f.ddr_generation),
    memorySlots: num(f.memory_slots),
    hasWifi: Boolean(f.has_wifi),
    m2Slots: num(f.m2_slots),
    m2PcieGen: num(f.m2_pcie_gen),
    chipset: str(f.chipset),
    maxMemoryGb: num(f.max_memory_gb),
    sataPorts: num(f.sata_ports),
    pcieX16Slots: num(f.pcie_x16_slots),
    pcieGeneration: num(f.pcie_generation),
    hasBluetooth: Boolean(f.has_bluetooth),
    usbTypeACount: num(f.usb_type_a_count),
    usbTypeCCount: num(f.usb_type_c_count),
    audioCodec: str(f.audio_codec),
    supportsEcc: Boolean(f.supports_ecc),
    hasIpmi: Boolean(f.has_ipmi),
    memoryChannels: num(f.memory_channels),
    memoryModuleTypesInput: joinCommaList(strArr(f.memory_module_types)),
  };
}

export function cpuCoolerDefaults(f: Record<string, unknown>): ApproveCpuCoolerFormData {
  return {
    ...partDefaults(f),
    supportedSocketsInput: joinCommaList(strArr(f.supported_sockets)),
    coolerType: str(f.cooler_type),
    maxTdpWatts: num(f.max_tdp_watts),
    heightMm: num(f.height_mm),
    radiatorSizeMm: num(f.radiator_size_mm),
    fanCount: num(f.fan_count),
    fanSizeMm: num(f.fan_size_mm),
    noiseDba: num(f.noise_dba),
    hasRgb: Boolean(f.has_rgb),
  };
}

/** Suggested *_groups name when the reviewer is creating a new group: the spec
 * itself, since that is what the group *is*. Mirrors the naming the seeded
 * groups use ("DDR5-6000 CL30 32GB (2x16)"). */
function ramGroupName(f: Record<string, unknown>): string {
  const gen = str(f.ddr_generation).toUpperCase();
  const speed = num(f.speed_mhz);
  const cl = num(f.cas_latency);
  const cap = num(f.capacity_gb);
  const mods = num(f.modules);
  const per = num(f.module_capacity_gb);
  const bits = [
    speed ? `${gen}-${speed}` : gen,
    cl ? `CL${cl}` : '',
    cap ? `${cap}GB` : '',
    mods && per ? `(${mods}x${per})` : '',
    str(f.module_type).toUpperCase(),
  ];
  return bits.filter(Boolean).join(' ');
}

export function ramKitDefaults(f: Record<string, unknown>): ApproveRamKitFormData {
  return {
    ...partDefaults(f),
    groupId: '',
    groupName: ramGroupName(f),
    ddrGeneration: str(f.ddr_generation),
    speedMhz: num(f.speed_mhz),
    capacityGb: num(f.capacity_gb),
    modules: num(f.modules),
    moduleCapacityGb: num(f.module_capacity_gb),
    casLatency: num(f.cas_latency),
    voltage: num(f.voltage),
    // Registered memory is ECC by definition, so a page that stated the module
    // type but not is_ecc still prefills the box correctly.
    isEcc: Boolean(f.is_ecc) || ['rdimm', 'lrdimm'].includes(str(f.module_type)),
    moduleType: str(f.module_type),
    heightMm: num(f.height_mm),
    hasRgb: Boolean(f.has_rgb),
  };
}

export function storageDriveDefaults(
  f: Record<string, unknown>,
): ApproveStorageDriveFormData {
  const cap = num(f.capacity_gb);
  const iface = str(f.interface);
  return {
    ...partDefaults(f),
    groupId: '',
    groupName: [cap ? `${cap}GB` : '', iface, str(f.storage_type).toUpperCase()]
      .filter(Boolean)
      .join(' '),
    storageType: str(f.storage_type),
    formFactor: str(f.form_factor),
    interface: iface,
    capacityGb: cap,
    readSpeedMbps: num(f.read_speed_mbps),
    writeSpeedMbps: num(f.write_speed_mbps),
    hasDramCache: Boolean(f.has_dram_cache),
    enduranceTbw: num(f.endurance_tbw),
    rpm: num(f.rpm),
  };
}

export function psuDefaults(f: Record<string, unknown>): ApprovePsuFormData {
  const watts = num(f.wattage);
  return {
    ...partDefaults(f),
    groupId: '',
    groupName: [
      watts ? `${watts}W` : '',
      str(f.efficiency_rating),
      str(f.form_factor).toUpperCase(),
      str(f.modular) ? `${str(f.modular)}-modular` : '',
    ]
      .filter(Boolean)
      .join(' '),
    wattage: watts,
    formFactor: str(f.form_factor),
    efficiencyRating: str(f.efficiency_rating),
    modular: str(f.modular),
    isFanless: Boolean(f.is_fanless),
    fanSizeMm: num(f.fan_size_mm),
    pcie8pinConnectors: num(f.pcie_8pin_connectors),
    pcie12pinConnectors: num(f.pcie_12pin_connectors),
    pcie16pinConnectors: num(f.pcie_16pin_connectors),
    epsConnectors: num(f.eps_connectors),
    depthMm: num(f.depth_mm),
  };
}

export function caseDefaults(f: Record<string, unknown>): ApproveCaseFormData {
  return {
    ...partDefaults(f),
    supportedMoboFormFactorsInput: joinCommaList(strArr(f.supported_mobo_form_factors)),
    size: str(f.size),
    maxGpuLengthMm: num(f.max_gpu_length_mm),
    maxCoolerHeightMm: num(f.max_cooler_height_mm),
    maxRadiatorFrontMm: num(f.max_radiator_front_mm),
    maxRadiatorTopMm: num(f.max_radiator_top_mm),
    maxPsuLengthMm: num(f.max_psu_length_mm),
    includedFanCount: num(f.included_fan_count),
    chamberCount: num(f.chamber_count),
    frontPanelMesh: Boolean(f.front_panel_mesh),
    color: str(f.color),
    driveBays35: num(f.drive_bays_35),
    driveBays25: num(f.drive_bays_25),
    maxFanSlots: num(f.max_fan_slots),
    hasGlassPanel: Boolean(f.has_glass_panel),
    weightKg: num(f.weight_kg),
    lengthMm: num(f.length_mm),
    widthMm: num(f.width_mm),
    heightMm: num(f.height_mm),
    usbFrontTypeA: num(f.usb_front_type_a),
    usbFrontTypeC: num(f.usb_front_type_c),
  };
}

export function fanDefaults(f: Record<string, unknown>): ApproveFanFormData {
  return {
    ...partDefaults(f),
    sizeMm: num(f.size_mm),
    maxRpm: num(f.max_rpm),
    airflowCfm: num(f.airflow_cfm),
    noiseDba: num(f.noise_dba),
    isPwm: Boolean(f.is_pwm),
    hasRgb: Boolean(f.has_rgb),
    bearingType: str(f.bearing_type),
    isStaticPressure: Boolean(f.is_static_pressure),
    packCount: num(f.pack_count),
  };
}

export function aiModelDefaults(f: Record<string, unknown>): ApproveAiModelFormData {
  const name = str(f.name);
  return {
    name,
    // The Hugging Face path already derives a slug from the Hub id, which is
    // the more stable source; fall back to the display name for models staged
    // from a vendor page instead.
    slug: str(f.slug) || slugify(name),
    family: str(f.family),
    paramsBillions: num(f.params_billions),
    contextLength: num(f.context_length),
    developer: str(f.developer),
    license: str(f.license),
    huggingfaceId: str(f.huggingface_id),
    websiteUrl: str(f.website_url),
    notes: str(f.notes),
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

// Shared across the eight pc_parts-backed approvals.
const partSchemaFields = {
  name: z.string().min(1, 'Name is required'),
  manufacturer: z.string(),
  modelNumber: z.string(),
  yearReleased: z.number().int().nullable(),
  msrpUsd: z.number().nullable(),
};

const motherboardSchema = z.object({
  ...partSchemaFields,
  socket: z.string().min(1, 'Socket is required'),
  formFactor: z.string().min(1, 'Form factor is required'),
  ddrGeneration: z.string().min(1, 'DDR generation is required'),
  memorySlots: requiredInt('Memory slots'),
  hasWifi: z.boolean(),
  m2Slots: z.number().int().nullable(),
  m2PcieGen: z.number().int().nullable(),
  chipset: z.string(),
  maxMemoryGb: z.number().int().nullable(),
  sataPorts: z.number().int().nullable(),
  pcieX16Slots: z.number().int().nullable(),
  pcieGeneration: z.number().int().nullable(),
  hasBluetooth: z.boolean(),
  usbTypeACount: z.number().int().nullable(),
  usbTypeCCount: z.number().int().nullable(),
  audioCodec: z.string(),
  supportsEcc: z.boolean(),
  hasIpmi: z.boolean(),
  memoryChannels: z.number().int().nullable(),
  memoryModuleTypesInput: z.string(),
});

const cpuCoolerSchema = z.object({
  ...partSchemaFields,
  supportedSocketsInput: z.string().min(1, 'Supported sockets are required'),
  coolerType: z.string().min(1, 'Cooler type is required'),
  maxTdpWatts: z.number().int().nullable(),
  heightMm: z.number().int().nullable(),
  radiatorSizeMm: z.number().int().nullable(),
  fanCount: z.number().int().nullable(),
  fanSizeMm: z.number().int().nullable(),
  noiseDba: z.number().nullable(),
  hasRgb: z.boolean(),
});

// The grouped categories validate their group spec unconditionally rather
// than only when creating a new group. Picking an existing group ignores
// those fields, but a reviewer who then switches back to "create new" would
// otherwise get a silently half-filled group.
const ramKitSchema = z.object({
  ...partSchemaFields,
  groupId: z.string(),
  groupName: z.string(),
  ddrGeneration: z.string().min(1, 'DDR generation is required'),
  speedMhz: requiredInt('Speed'),
  capacityGb: requiredInt('Capacity'),
  modules: requiredInt('Modules'),
  moduleCapacityGb: z.number().int().nullable(),
  casLatency: z.number().int().nullable(),
  voltage: z.number().nullable(),
  isEcc: z.boolean(),
  moduleType: z.string(),
  heightMm: z.number().int().nullable(),
  hasRgb: z.boolean(),
});

const storageDriveSchema = z.object({
  ...partSchemaFields,
  groupId: z.string(),
  groupName: z.string(),
  storageType: z.string().min(1, 'Storage type is required'),
  formFactor: z.string().min(1, 'Form factor is required'),
  interface: z.string().min(1, 'Interface is required'),
  capacityGb: requiredInt('Capacity'),
  readSpeedMbps: z.number().int().nullable(),
  writeSpeedMbps: z.number().int().nullable(),
  hasDramCache: z.boolean(),
  enduranceTbw: z.number().int().nullable(),
  rpm: z.number().int().nullable(),
});

const psuSchema = z.object({
  ...partSchemaFields,
  groupId: z.string(),
  groupName: z.string(),
  wattage: requiredInt('Wattage'),
  formFactor: z.string().min(1, 'Form factor is required'),
  efficiencyRating: z.string().min(1, 'Efficiency rating is required'),
  modular: z.string(),
  isFanless: z.boolean(),
  fanSizeMm: z.number().int().nullable(),
  pcie8pinConnectors: z.number().int().nullable(),
  pcie12pinConnectors: z.number().int().nullable(),
  pcie16pinConnectors: z.number().int().nullable(),
  epsConnectors: z.number().int().nullable(),
  depthMm: z.number().int().nullable(),
});

const caseSchema = z.object({
  ...partSchemaFields,
  supportedMoboFormFactorsInput: z.string().min(1, 'Supported board sizes are required'),
  size: z.string().min(1, 'Size is required'),
  maxGpuLengthMm: requiredInt('Max GPU length'),
  maxCoolerHeightMm: requiredInt('Max cooler height'),
  maxRadiatorFrontMm: z.number().int().nullable(),
  maxRadiatorTopMm: z.number().int().nullable(),
  maxPsuLengthMm: z.number().int().nullable(),
  includedFanCount: z.number().int().nullable(),
  chamberCount: z.number().int().nullable(),
  frontPanelMesh: z.boolean(),
  color: z.string(),
  driveBays35: z.number().int().nullable(),
  driveBays25: z.number().int().nullable(),
  maxFanSlots: z.number().int().nullable(),
  hasGlassPanel: z.boolean(),
  weightKg: z.number().nullable(),
  lengthMm: z.number().int().nullable(),
  widthMm: z.number().int().nullable(),
  heightMm: z.number().int().nullable(),
  usbFrontTypeA: z.number().int().nullable(),
  usbFrontTypeC: z.number().int().nullable(),
});

const fanSchema = z.object({
  ...partSchemaFields,
  sizeMm: requiredInt('Size'),
  maxRpm: z.number().int().nullable(),
  airflowCfm: z.number().nullable(),
  noiseDba: z.number().nullable(),
  isPwm: z.boolean(),
  hasRgb: z.boolean(),
  bearingType: z.string(),
  isStaticPressure: z.boolean(),
  packCount: z.number().int().nullable(),
});

const aiModelSchema = z.object({
  name: z.string().min(1, 'Name is required'),
  slug: z.string().min(1, 'Slug is required'),
  family: z.string().min(1, 'Family is required'),
  paramsBillions: z.number().nullable(),
  contextLength: z.number().int().nullable(),
  developer: z.string(),
  license: z.string(),
  huggingfaceId: z.string(),
  websiteUrl: z.string(),
  notes: z.string(),
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

function CheckboxField({
  control,
  name,
  label,
}: {
  control: any;
  name: string;
  label: string;
}) {
  return (
    <FormField
      control={control}
      name={name}
      render={({ field }) => (
        <FormItem className="flex items-center gap-2 space-y-0">
          <FormControl>
            <Checkbox checked={field.value} onCheckedChange={field.onChange} />
          </FormControl>
          <FormLabel>{label}</FormLabel>
        </FormItem>
      )}
    />
  );
}

/** Group picker for RAM/storage/PSU. Empty value means "create a new group
 * from the spec fields below", which is the right default for a discovered
 * part — the whole point of discovering it is that it's new. */
function GroupField({
  control,
  groups,
  label,
}: {
  control: any;
  groups: GroupOption[];
  label: string;
}) {
  return (
    <FormField
      control={control}
      name="groupId"
      render={({ field }) => (
        <FormItem className="col-span-2">
          <FormLabel>{label}</FormLabel>
          <Select value={field.value || NEW_GROUP} onValueChange={(v) =>
            field.onChange(v === NEW_GROUP ? '' : v)
          }>
            <FormControl>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
            </FormControl>
            <SelectContent>
              <SelectItem value={NEW_GROUP}>Create a new group from the spec below</SelectItem>
              {groups.map((g) => (
                <SelectItem key={g.id} value={g.id}>
                  {g.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {!field.value && (
            <p className="text-xs text-muted-foreground">
              Attach to an existing group instead if this SKU shares a spec with one —
              price and every recommender query live on the group.
            </p>
          )}
          <FormMessage />
        </FormItem>
      )}
    />
  );
}

// Radix Select rejects an empty-string item value, so the "new group" choice
// needs a real sentinel; GroupField maps it back to '' for the action.
const NEW_GROUP = '__new__';

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
        {/* Mention ddr6 in the label when DDR6 parts exist. */}
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

// ---------------------------------------------------------------------------
// The remaining categories
// ---------------------------------------------------------------------------
// All follow the shape the three above established: prefill from
// extractedFields, validate against the subtype's NOT NULL columns, call the
// matching server action, surface its error verbatim. Free-text enum fields
// (socket, form factor, interface) label their vocabulary inline rather than
// using a Select — the backend stores them as free text precisely because the
// vocabulary keeps growing, and a fixed dropdown would block the first part
// that arrives with a new socket name.

export function ApproveMotherboardForm({
  itemId,
  extractedFields,
  onSuccess,
}: {
  itemId: string;
  extractedFields: Record<string, unknown>;
  onSuccess: () => void;
}) {
  const form = useForm<ApproveMotherboardFormData>({
    resolver: zodResolver(motherboardSchema),
    defaultValues: motherboardDefaults(extractedFields),
  });
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(data: ApproveMotherboardFormData) {
    setError(null);
    const res = await approveMotherboard(itemId, data);
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
          <TextField control={form.control} name="socket" label="Socket * (e.g. AM5, sTR5, LGA1851)" />
          <TextField
            control={form.control}
            name="formFactor"
            label="Form Factor * (atx | matx | itx | eatx | ssi_eeb | ssi_ceb)"
          />
          <TextField control={form.control} name="ddrGeneration" label="DDR Generation * (ddr4 | ddr5)" />
          <NumberField control={form.control} name="memorySlots" label="Memory Slots *" />
          <NumberField control={form.control} name="memoryChannels" label="Memory Channels" />
          <TextField control={form.control} name="chipset" label="Chipset (e.g. TRX50, X870E)" />
          <NumberField control={form.control} name="maxMemoryGb" label="Max Memory (GB)" />
          <NumberField control={form.control} name="m2Slots" label="M.2 Slots" />
          <NumberField control={form.control} name="m2PcieGen" label="M.2 PCIe Gen" />
          <NumberField control={form.control} name="sataPorts" label="SATA Ports" />
          <NumberField control={form.control} name="pcieX16Slots" label="PCIe x16 Slots" />
          <NumberField control={form.control} name="pcieGeneration" label="PCIe Generation" />
          <NumberField control={form.control} name="usbTypeACount" label="USB Type-A Ports" />
          <NumberField control={form.control} name="usbTypeCCount" label="USB Type-C Ports" />
          <TextField control={form.control} name="audioCodec" label="Audio Codec" />
        </div>
        <TextField
          control={form.control}
          name="memoryModuleTypesInput"
          label="Accepted Module Types (comma-separated: udimm, rdimm, lrdimm — blank = unconstrained)"
        />
        <div className="flex flex-wrap gap-6">
          <CheckboxField control={form.control} name="hasWifi" label="Wi-Fi" />
          <CheckboxField control={form.control} name="hasBluetooth" label="Bluetooth" />
          <CheckboxField control={form.control} name="supportsEcc" label="Supports ECC" />
          <CheckboxField control={form.control} name="hasIpmi" label="IPMI / BMC" />
        </div>
        <SubmitRow error={error} isSubmitting={form.formState.isSubmitting} />
      </form>
    </Form>
  );
}

export function ApproveCpuCoolerForm({
  itemId,
  extractedFields,
  onSuccess,
}: {
  itemId: string;
  extractedFields: Record<string, unknown>;
  onSuccess: () => void;
}) {
  const form = useForm<ApproveCpuCoolerFormData>({
    resolver: zodResolver(cpuCoolerSchema),
    defaultValues: cpuCoolerDefaults(extractedFields),
  });
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(data: ApproveCpuCoolerFormData) {
    setError(null);
    const res = await approveCpuCooler(itemId, data);
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
          <TextField
            control={form.control}
            name="coolerType"
            label="Type * (air | aio_120 | aio_140 | aio_240 | aio_280 | aio_360)"
          />
          <NumberField control={form.control} name="maxTdpWatts" label="Max TDP (Watts)" />
          <NumberField control={form.control} name="heightMm" label="Height (mm, air)" />
          <NumberField control={form.control} name="radiatorSizeMm" label="Radiator (mm, liquid)" />
          <NumberField control={form.control} name="fanCount" label="Fan Count" />
          <NumberField control={form.control} name="fanSizeMm" label="Fan Size (mm)" />
          <NumberField control={form.control} name="noiseDba" label="Noise (dBA)" step="0.1" />
        </div>
        <TextField
          control={form.control}
          name="supportedSocketsInput"
          label="Supported Sockets * (comma-separated, e.g. AM5, sTR5, SP6)"
        />
        <CheckboxField control={form.control} name="hasRgb" label="RGB" />
        <SubmitRow error={error} isSubmitting={form.formState.isSubmitting} />
      </form>
    </Form>
  );
}

export function ApproveRamKitForm({
  itemId,
  extractedFields,
  groups,
  onSuccess,
}: {
  itemId: string;
  extractedFields: Record<string, unknown>;
  groups: GroupOption[];
  onSuccess: () => void;
}) {
  const form = useForm<ApproveRamKitFormData>({
    resolver: zodResolver(ramKitSchema),
    defaultValues: ramKitDefaults(extractedFields),
  });
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(data: ApproveRamKitFormData) {
    setError(null);
    const res = await approveRamKit(itemId, data);
    if (res.error) setError(res.error);
    else onSuccess();
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <TextField control={form.control} name="name" label="Kit Name *" />
          <TextField control={form.control} name="manufacturer" label="Manufacturer" />
          <TextField control={form.control} name="modelNumber" label="Model Number" />
          <NumberField control={form.control} name="yearReleased" label="Year Released" />
          <NumberField control={form.control} name="msrpUsd" label="MSRP (USD)" step="0.01" />
          <NumberField control={form.control} name="heightMm" label="Height (mm)" />
          <GroupField control={form.control} groups={groups} label="RAM Group" />
          <TextField control={form.control} name="groupName" label="New Group Name" />
          <TextField control={form.control} name="ddrGeneration" label="DDR Generation * (ddr4 | ddr5)" />
          <NumberField control={form.control} name="speedMhz" label="Speed (MT/s) *" />
          <NumberField control={form.control} name="capacityGb" label="Total Capacity (GB) *" />
          <NumberField control={form.control} name="modules" label="Modules *" />
          <NumberField control={form.control} name="moduleCapacityGb" label="Per-Module (GB)" />
          <NumberField control={form.control} name="casLatency" label="CAS Latency" />
          <NumberField control={form.control} name="voltage" label="Voltage" step="0.01" />
          <TextField
            control={form.control}
            name="moduleType"
            label="Module Type (udimm | rdimm | lrdimm)"
          />
        </div>
        <div className="flex flex-wrap gap-6">
          <CheckboxField control={form.control} name="isEcc" label="ECC" />
          <CheckboxField control={form.control} name="hasRgb" label="RGB" />
        </div>
        <SubmitRow error={error} isSubmitting={form.formState.isSubmitting} />
      </form>
    </Form>
  );
}

export function ApproveStorageDriveForm({
  itemId,
  extractedFields,
  groups,
  onSuccess,
}: {
  itemId: string;
  extractedFields: Record<string, unknown>;
  groups: GroupOption[];
  onSuccess: () => void;
}) {
  const form = useForm<ApproveStorageDriveFormData>({
    resolver: zodResolver(storageDriveSchema),
    defaultValues: storageDriveDefaults(extractedFields),
  });
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(data: ApproveStorageDriveFormData) {
    setError(null);
    const res = await approveStorageDrive(itemId, data);
    if (res.error) setError(res.error);
    else onSuccess();
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <TextField control={form.control} name="name" label="Drive Name *" />
          <TextField control={form.control} name="manufacturer" label="Manufacturer" />
          <TextField control={form.control} name="modelNumber" label="Model Number" />
          <NumberField control={form.control} name="yearReleased" label="Year Released" />
          <NumberField control={form.control} name="msrpUsd" label="MSRP (USD)" step="0.01" />
          <GroupField control={form.control} groups={groups} label="Storage Group" />
          <TextField control={form.control} name="groupName" label="New Group Name" />
          <TextField control={form.control} name="storageType" label="Type * (nvme | ssd | hdd)" />
          <TextField control={form.control} name="formFactor" label="Form Factor * (e.g. m2_2280, 2_5, 3_5)" />
          <TextField
            control={form.control}
            name="interface"
            label="Interface * (pcie_gen3 | pcie_gen4 | pcie_gen5 | sata3)"
          />
          <NumberField control={form.control} name="capacityGb" label="Capacity (GB) *" />
          <NumberField control={form.control} name="readSpeedMbps" label="Read (MB/s)" />
          <NumberField control={form.control} name="writeSpeedMbps" label="Write (MB/s)" />
          <NumberField control={form.control} name="enduranceTbw" label="Endurance (TBW)" />
          <NumberField control={form.control} name="rpm" label="RPM (spinning disks)" />
        </div>
        <CheckboxField control={form.control} name="hasDramCache" label="DRAM Cache" />
        <SubmitRow error={error} isSubmitting={form.formState.isSubmitting} />
      </form>
    </Form>
  );
}

export function ApprovePsuForm({
  itemId,
  extractedFields,
  groups,
  onSuccess,
}: {
  itemId: string;
  extractedFields: Record<string, unknown>;
  groups: GroupOption[];
  onSuccess: () => void;
}) {
  const form = useForm<ApprovePsuFormData>({
    resolver: zodResolver(psuSchema),
    defaultValues: psuDefaults(extractedFields),
  });
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(data: ApprovePsuFormData) {
    setError(null);
    const res = await approvePsu(itemId, data);
    if (res.error) setError(res.error);
    else onSuccess();
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <TextField control={form.control} name="name" label="Unit Name *" />
          <TextField control={form.control} name="manufacturer" label="Manufacturer" />
          <TextField control={form.control} name="modelNumber" label="Model Number" />
          <NumberField control={form.control} name="yearReleased" label="Year Released" />
          <NumberField control={form.control} name="msrpUsd" label="MSRP (USD)" step="0.01" />
          <NumberField control={form.control} name="depthMm" label="Depth (mm)" />
          <GroupField control={form.control} groups={groups} label="PSU Group" />
          <TextField control={form.control} name="groupName" label="New Group Name" />
          <NumberField control={form.control} name="wattage" label="Wattage *" />
          <TextField control={form.control} name="formFactor" label="Form Factor * (atx | sfx | sfx_l)" />
          <TextField
            control={form.control}
            name="efficiencyRating"
            label="Efficiency * (80plus | 80plus_bronze | ... | 80plus_titanium)"
          />
          <TextField control={form.control} name="modular" label="Modular (full | semi | non)" />
          <NumberField control={form.control} name="fanSizeMm" label="Fan Size (mm)" />
          <NumberField control={form.control} name="pcie8pinConnectors" label="PCIe 8-pin" />
          <NumberField control={form.control} name="pcie12pinConnectors" label="PCIe 12-pin" />
          <NumberField control={form.control} name="pcie16pinConnectors" label="PCIe 16-pin (12VHPWR)" />
          <NumberField control={form.control} name="epsConnectors" label="EPS (CPU) Connectors" />
        </div>
        <CheckboxField control={form.control} name="isFanless" label="Fanless" />
        <SubmitRow error={error} isSubmitting={form.formState.isSubmitting} />
      </form>
    </Form>
  );
}

export function ApproveCaseForm({
  itemId,
  extractedFields,
  onSuccess,
}: {
  itemId: string;
  extractedFields: Record<string, unknown>;
  onSuccess: () => void;
}) {
  const form = useForm<ApproveCaseFormData>({
    resolver: zodResolver(caseSchema),
    defaultValues: caseDefaults(extractedFields),
  });
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(data: ApproveCaseFormData) {
    setError(null);
    const res = await approveCase(itemId, data);
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
          <TextField
            control={form.control}
            name="size"
            label="Size * (full_tower | mid_tower | mini_tower | sff)"
          />
          <NumberField control={form.control} name="maxGpuLengthMm" label="Max GPU Length (mm) *" />
          <NumberField control={form.control} name="maxCoolerHeightMm" label="Max Cooler Height (mm) *" />
          <NumberField control={form.control} name="maxRadiatorFrontMm" label="Max Radiator Front (mm)" />
          <NumberField control={form.control} name="maxRadiatorTopMm" label="Max Radiator Top (mm)" />
          <NumberField control={form.control} name="maxPsuLengthMm" label="Max PSU Length (mm)" />
          <NumberField control={form.control} name="includedFanCount" label="Included Fans" />
          <NumberField control={form.control} name="maxFanSlots" label="Max Fan Slots" />
          <NumberField control={form.control} name="chamberCount" label="Chambers" />
          <NumberField control={form.control} name="driveBays35" label='3.5" Bays' />
          <NumberField control={form.control} name="driveBays25" label='2.5" Bays' />
          <NumberField control={form.control} name="weightKg" label="Weight (kg)" step="0.1" />
          <NumberField control={form.control} name="lengthMm" label="Length (mm)" />
          <NumberField control={form.control} name="widthMm" label="Width (mm)" />
          <NumberField control={form.control} name="heightMm" label="Height (mm)" />
          <NumberField control={form.control} name="usbFrontTypeA" label="Front USB Type-A" />
          <NumberField control={form.control} name="usbFrontTypeC" label="Front USB Type-C" />
          <TextField control={form.control} name="color" label="Color" />
        </div>
        <TextField
          control={form.control}
          name="supportedMoboFormFactorsInput"
          label="Supported Board Sizes * (comma-separated, e.g. atx, eatx, ssi_eeb)"
        />
        <div className="flex flex-wrap gap-6">
          <CheckboxField control={form.control} name="frontPanelMesh" label="Mesh Front Panel" />
          <CheckboxField control={form.control} name="hasGlassPanel" label="Glass Panel" />
        </div>
        <SubmitRow error={error} isSubmitting={form.formState.isSubmitting} />
      </form>
    </Form>
  );
}

export function ApproveFanForm({
  itemId,
  extractedFields,
  onSuccess,
}: {
  itemId: string;
  extractedFields: Record<string, unknown>;
  onSuccess: () => void;
}) {
  const form = useForm<ApproveFanFormData>({
    resolver: zodResolver(fanSchema),
    defaultValues: fanDefaults(extractedFields),
  });
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(data: ApproveFanFormData) {
    setError(null);
    const res = await approveFan(itemId, data);
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
          <NumberField control={form.control} name="sizeMm" label="Size (mm) *" />
          <NumberField control={form.control} name="maxRpm" label="Max RPM" />
          <NumberField control={form.control} name="airflowCfm" label="Airflow (CFM)" step="0.1" />
          <NumberField control={form.control} name="noiseDba" label="Noise (dBA)" step="0.1" />
          <NumberField control={form.control} name="packCount" label="Pack Count" />
          <TextField control={form.control} name="bearingType" label="Bearing Type" />
        </div>
        <div className="flex flex-wrap gap-6">
          <CheckboxField control={form.control} name="isPwm" label="PWM" />
          <CheckboxField control={form.control} name="hasRgb" label="RGB" />
          <CheckboxField control={form.control} name="isStaticPressure" label="Static Pressure" />
        </div>
        <SubmitRow error={error} isSubmitting={form.formState.isSubmitting} />
      </form>
    </Form>
  );
}

export function ApproveAiModelForm({
  itemId,
  extractedFields,
  onSuccess,
}: {
  itemId: string;
  extractedFields: Record<string, unknown>;
  onSuccess: () => void;
}) {
  const form = useForm<ApproveAiModelFormData>({
    resolver: zodResolver(aiModelSchema),
    defaultValues: aiModelDefaults(extractedFields),
  });
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(data: ApproveAiModelFormData) {
    setError(null);
    const res = await approveAiModel(itemId, data);
    if (res.error) setError(res.error);
    else onSuccess();
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <TextField control={form.control} name="name" label="Name *" />
          <TextField control={form.control} name="slug" label="Slug *" />
          <TextField
            control={form.control}
            name="family"
            label="Family * (llm | multimodal | image_gen | video_gen | speech | audio_gen | vision | embedding | classical | rl)"
          />
          <NumberField
            control={form.control}
            name="paramsBillions"
            label="Parameters (billions)"
            step="0.001"
          />
          <NumberField control={form.control} name="contextLength" label="Context Length (tokens)" />
          <TextField control={form.control} name="developer" label="Developer" />
          <TextField control={form.control} name="license" label="License" />
          <TextField control={form.control} name="huggingfaceId" label="Hugging Face ID" />
          <TextField control={form.control} name="websiteUrl" label="Website URL" />
        </div>
        <TextField control={form.control} name="notes" label="Notes" />
        <p className="text-xs text-muted-foreground">
          Approving creates the catalog entry only. VRAM floors come from
          ai_workloads rows (model × task × precision), which stay hand-authored —
          add them from the AI Models page once this is in.
        </p>
        <SubmitRow error={error} isSubmitting={form.formState.isSubmitting} />
      </form>
    </Form>
  );
}
