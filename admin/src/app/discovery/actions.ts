'use server';

import { revalidatePath } from 'next/cache';
import type { Prisma } from '@prisma/client';
import { db } from '@/lib/prisma';
import { splitCommaList, usdToCents } from '@/lib/utils';

// Unlike the other pages' actions, everything here returns { error?: string }
// instead of throwing — thrown server-action messages are masked in
// production, and approval failures (duplicate name, already reviewed) need
// to reach the reviewer verbatim.

export type DiscoveryCategory =
  | 'cpu'
  | 'gpu_chipset'
  | 'gpu_variant'
  | 'motherboard'
  | 'cpu_cooler'
  | 'ram_kit'
  | 'storage_drive'
  | 'psu'
  | 'case'
  | 'fan'
  | 'ai_model';

async function postDiscovery(
  path: string,
  body: Record<string, unknown>,
): Promise<{ runId?: string; error?: string }> {
  const base = process.env.BACKEND_API_URL;
  const key = process.env.DISCOVERY_API_KEY;
  if (!base || !key) {
    // In the cluster these come from the admin-config ConfigMap and the
    // palladium-secrets Secret, not this file — check the pod's env first.
    return { error: 'BACKEND_API_URL and DISCOVERY_API_KEY must be set in admin/.env.local' };
  }

  let res: Response;
  try {
    res = await fetch(`${base}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Admin-Key': key },
      body: JSON.stringify(body),
      cache: 'no-store',
    });
  } catch {
    return { error: `Cannot reach backend at ${base} — is it running?` };
  }

  if (res.status === 202) {
    const body = (await res.json()) as { run_id: string };
    revalidatePath('/discovery');
    return { runId: body.run_id };
  }

  let detail = '';
  try {
    detail = ((await res.json()) as { detail?: string }).detail ?? '';
  } catch {
    // non-JSON error body
  }
  if (res.status === 503) {
    return { error: `Backend not configured for discovery: ${detail || 'missing API keys'}` };
  }
  if (res.status === 403) {
    return { error: 'Backend rejected DISCOVERY_API_KEY — make sure admin and backend use the same value' };
  }
  return { error: `Discovery trigger failed (${res.status}): ${detail || res.statusText}` };
}

/** Discover one named part. */
export async function triggerDiscovery(
  query: string,
  category: DiscoveryCategory,
): Promise<{ runId?: string; error?: string }> {
  return postDiscovery('/api/v1/discovery/runs', { query, category });
}

/** Enumerate what's new in a category and discover each candidate. One run
 * row, up to DISCOVERY_SWEEP_MAX_CANDIDATES items — costs that many times a
 * single-part run, so the UI warns before firing it. */
export async function triggerSweep(
  hint: string,
  category: DiscoveryCategory,
): Promise<{ runId?: string; error?: string }> {
  return postDiscovery('/api/v1/discovery/sweeps', {
    category,
    hint: hint.trim() || null,
  });
}

export interface ApproveCpuFormData {
  name: string;
  manufacturer: string;
  modelNumber: string;
  yearReleased: number | null;
  msrpUsd: number | null;
  brand: string;
  socket: string;
  tdpWatts: number | null;
  hasIgpu: boolean;
  ddrGenerationInput: string;
  cores: number | null;
  threads: number | null;
  baseClockGhz: number | null;
  boostClockGhz: number | null;
  l3CacheMb: number | null;
  pcieGeneration: number | null;
  maxMemoryGb: number | null;
  series: string;
  supportedFeaturesInput: string;
}

export interface ApproveGpuChipsetFormData {
  name: string;
  vramGb: number | null;
  vramType: string;
  tdpWatts: number | null;
  recommendedPsuWatts: number | null;
  pcieGeneration: number | null;
  baseClockMhz: number | null;
  boostClockMhz: number | null;
  hasRayTracing: boolean;
  cudaCores: number | null;
  tensorCores: number | null;
  streamProcessors: number | null;
  matrixCores: number | null;
  supportedFeaturesInput: string;
}

export interface ApproveGpuVariantFormData {
  name: string;
  manufacturer: string;
  modelNumber: string;
  yearReleased: number | null;
  msrpUsd: number | null;
  gpuChipsetId: string;
  brand: string;
  lengthMm: number | null;
  widthSlots: number | null;
  pciePowerPins: string;
  displayOutputs: string;
  hdmiVersion: string;
  dpVersion: string;
}

export interface ApproveMotherboardFormData {
  name: string;
  manufacturer: string;
  modelNumber: string;
  yearReleased: number | null;
  msrpUsd: number | null;
  socket: string;
  formFactor: string;
  ddrGeneration: string;
  memorySlots: number | null;
  hasWifi: boolean;
  m2Slots: number | null;
  m2PcieGen: number | null;
  chipset: string;
  maxMemoryGb: number | null;
  sataPorts: number | null;
  pcieX16Slots: number | null;
  pcieGeneration: number | null;
  hasBluetooth: boolean;
  usbTypeACount: number | null;
  usbTypeCCount: number | null;
  audioCodec: string;
  supportsEcc: boolean;
  hasIpmi: boolean;
  memoryChannels: number | null;
  memoryModuleTypesInput: string;
}

export interface ApproveCpuCoolerFormData {
  name: string;
  manufacturer: string;
  modelNumber: string;
  yearReleased: number | null;
  msrpUsd: number | null;
  supportedSocketsInput: string;
  coolerType: string;
  maxTdpWatts: number | null;
  heightMm: number | null;
  radiatorSizeMm: number | null;
  fanCount: number | null;
  fanSizeMm: number | null;
  noiseDba: number | null;
  hasRgb: boolean;
}

/** RAM/storage/PSU approvals write two rows: the pc_parts SKU and the
 * *_groups row carrying the spec every SKU with that spec shares. `groupId`
 * is the reviewer's answer to "which group is this?" — set when they picked an
 * existing one, empty to create a new group from the fields below it. */
export interface ApproveRamKitFormData {
  name: string;
  manufacturer: string;
  modelNumber: string;
  yearReleased: number | null;
  msrpUsd: number | null;
  groupId: string;
  groupName: string;
  ddrGeneration: string;
  speedMhz: number | null;
  capacityGb: number | null;
  modules: number | null;
  moduleCapacityGb: number | null;
  casLatency: number | null;
  voltage: number | null;
  isEcc: boolean;
  moduleType: string;
  heightMm: number | null;
  hasRgb: boolean;
}

export interface ApproveStorageDriveFormData {
  name: string;
  manufacturer: string;
  modelNumber: string;
  yearReleased: number | null;
  msrpUsd: number | null;
  groupId: string;
  groupName: string;
  storageType: string;
  formFactor: string;
  interface: string;
  capacityGb: number | null;
  readSpeedMbps: number | null;
  writeSpeedMbps: number | null;
  hasDramCache: boolean;
  enduranceTbw: number | null;
  rpm: number | null;
}

export interface ApprovePsuFormData {
  name: string;
  manufacturer: string;
  modelNumber: string;
  yearReleased: number | null;
  msrpUsd: number | null;
  groupId: string;
  groupName: string;
  wattage: number | null;
  formFactor: string;
  efficiencyRating: string;
  modular: string;
  isFanless: boolean;
  fanSizeMm: number | null;
  pcie8pinConnectors: number | null;
  pcie12pinConnectors: number | null;
  pcie16pinConnectors: number | null;
  epsConnectors: number | null;
  depthMm: number | null;
}

export interface ApproveCaseFormData {
  name: string;
  manufacturer: string;
  modelNumber: string;
  yearReleased: number | null;
  msrpUsd: number | null;
  supportedMoboFormFactorsInput: string;
  size: string;
  maxGpuLengthMm: number | null;
  maxCoolerHeightMm: number | null;
  maxRadiatorFrontMm: number | null;
  maxRadiatorTopMm: number | null;
  maxPsuLengthMm: number | null;
  includedFanCount: number | null;
  chamberCount: number | null;
  frontPanelMesh: boolean;
  color: string;
  driveBays35: number | null;
  driveBays25: number | null;
  maxFanSlots: number | null;
  hasGlassPanel: boolean;
  weightKg: number | null;
  lengthMm: number | null;
  widthMm: number | null;
  heightMm: number | null;
  usbFrontTypeA: number | null;
  usbFrontTypeC: number | null;
}

export interface ApproveFanFormData {
  name: string;
  manufacturer: string;
  modelNumber: string;
  yearReleased: number | null;
  msrpUsd: number | null;
  sizeMm: number | null;
  maxRpm: number | null;
  airflowCfm: number | null;
  noiseDba: number | null;
  isPwm: boolean;
  hasRgb: boolean;
  bearingType: string;
  isStaticPressure: boolean;
  packCount: number | null;
}

export interface ApproveAiModelFormData {
  name: string;
  slug: string;
  family: string;
  paramsBillions: number | null;
  contextLength: number | null;
  developer: string;
  license: string;
  huggingfaceId: string;
  websiteUrl: string;
  notes: string;
}

/** Flip the pending item to approved inside the same transaction that created
 * the catalog row; count 0 means someone else reviewed it first — throw so the
 * created row rolls back. */
async function markApproved(
  tx: Prisma.TransactionClient,
  itemId: string,
  link: {
    createdPartId?: string;
    createdChipsetId?: string;
    createdAiModelId?: string;
  },
) {
  const { count } = await tx.discoveredItem.updateMany({
    where: { id: itemId, reviewStatus: 'pending' },
    data: { reviewStatus: 'approved', reviewedAt: new Date(), ...link },
  });
  if (count === 0) throw new Error('Item was already reviewed');
}

export async function approveCpu(
  itemId: string,
  data: ApproveCpuFormData,
): Promise<{ error?: string }> {
  try {
    await db.$transaction(async (tx) => {
      const existing = await tx.pcPart.findFirst({
        where: { partType: 'cpu', name: { equals: data.name, mode: 'insensitive' } },
        select: { id: true },
      });
      if (existing) {
        throw new Error(`A CPU named "${data.name}" already exists — use Mark duplicate instead`);
      }
      const part = await tx.pcPart.create({
        data: {
          name: data.name,
          manufacturer: data.manufacturer || null,
          modelNumber: data.modelNumber || null,
          yearReleased: data.yearReleased,
          msrpCents: usdToCents(data.msrpUsd),
          isActive: true,
          partType: 'cpu',
          cpu: {
            create: {
              brand: data.brand || null,
              socket: data.socket || null,
              tdpWatts: data.tdpWatts,
              hasIgpu: data.hasIgpu,
              ddrGeneration: splitCommaList(data.ddrGenerationInput),
              cores: data.cores,
              threads: data.threads,
              baseClockGhz: data.baseClockGhz,
              boostClockGhz: data.boostClockGhz,
              l3CacheMb: data.l3CacheMb,
              pcieGeneration: data.pcieGeneration,
              maxMemoryGb: data.maxMemoryGb,
              series: data.series || null,
              supportedFeatures: splitCommaList(data.supportedFeaturesInput),
            },
          },
        },
      });
      await markApproved(tx, itemId, { createdPartId: part.id });
    });
  } catch (e) {
    return { error: e instanceof Error ? e.message : 'Approval failed' };
  }
  revalidatePath('/discovery');
  revalidatePath('/cpus');
  return {};
}

export async function approveGpuChipset(
  itemId: string,
  data: ApproveGpuChipsetFormData,
): Promise<{ error?: string }> {
  try {
    await db.$transaction(async (tx) => {
      const existing = await tx.gpuChipset.findFirst({
        where: { name: { equals: data.name, mode: 'insensitive' } },
        select: { id: true },
      });
      if (existing) {
        throw new Error(`A chipset named "${data.name}" already exists — use Mark duplicate instead`);
      }
      const chipset = await tx.gpuChipset.create({
        data: {
          name: data.name,
          vramGb: data.vramGb ?? 0,
          vramType: data.vramType || null,
          tdpWatts: data.tdpWatts ?? 0,
          recommendedPsuWatts: data.recommendedPsuWatts,
          pcieGeneration: data.pcieGeneration,
          baseClockMhz: data.baseClockMhz,
          boostClockMhz: data.boostClockMhz,
          hasRayTracing: data.hasRayTracing,
          cudaCores: data.cudaCores,
          tensorCores: data.tensorCores,
          streamProcessors: data.streamProcessors,
          matrixCores: data.matrixCores,
          supportedFeatures: splitCommaList(data.supportedFeaturesInput),
        },
      });
      await markApproved(tx, itemId, { createdChipsetId: chipset.id });
    });
  } catch (e) {
    return { error: e instanceof Error ? e.message : 'Approval failed' };
  }
  revalidatePath('/discovery');
  revalidatePath('/gpu-chipsets');
  return {};
}

export async function approveGpuVariant(
  itemId: string,
  data: ApproveGpuVariantFormData,
): Promise<{ error?: string }> {
  try {
    await db.$transaction(async (tx) => {
      const existing = await tx.pcPart.findFirst({
        where: { partType: 'gpu', name: { equals: data.name, mode: 'insensitive' } },
        select: { id: true },
      });
      if (existing) {
        throw new Error(`A GPU named "${data.name}" already exists — use Mark duplicate instead`);
      }
      const part = await tx.pcPart.create({
        data: {
          name: data.name,
          manufacturer: data.manufacturer || null,
          modelNumber: data.modelNumber || null,
          yearReleased: data.yearReleased,
          msrpCents: usdToCents(data.msrpUsd),
          isActive: true,
          partType: 'gpu',
          gpu: {
            create: {
              gpuChipsetId: data.gpuChipsetId,
              brand: data.brand,
              lengthMm: data.lengthMm ?? 0,
              widthSlots: data.widthSlots,
              pciePowerPins: data.pciePowerPins || null,
              displayOutputs: data.displayOutputs || null,
              hdmiVersion: data.hdmiVersion || null,
              dpVersion: data.dpVersion || null,
            },
          },
        },
      });
      await markApproved(tx, itemId, { createdPartId: part.id });
    });
  } catch (e) {
    return { error: e instanceof Error ? e.message : 'Approval failed' };
  }
  revalidatePath('/discovery');
  revalidatePath('/gpus');
  return {};
}

type PcPartCreateData = Parameters<typeof db.pcPart.create>[0]['data'];

/** Shared shape of every pc_parts-backed approval: reject a name collision up
 * front (that is a "Mark duplicate", not an approve), create the part with its
 * subtype row, then flip the queue item inside the same transaction. */
async function approvePart(
  itemId: string,
  partType: string,
  name: string,
  label: string,
  revalidate: string,
  // Prisma's create data is a checked/unchecked union; deriving it from the
  // client keeps the subtype nested-creates able to set their group FK
  // directly, exactly like the per-part-type pages do.
  build: (tx: Prisma.TransactionClient) => Promise<PcPartCreateData> | PcPartCreateData,
): Promise<{ error?: string }> {
  try {
    await db.$transaction(async (tx) => {
      const existing = await tx.pcPart.findFirst({
        where: { partType, name: { equals: name, mode: 'insensitive' } },
        select: { id: true },
      });
      if (existing) {
        throw new Error(
          `A ${label} named "${name}" already exists — use Mark duplicate instead`,
        );
      }
      const part = await tx.pcPart.create({ data: await build(tx) });
      await markApproved(tx, itemId, { createdPartId: part.id });
    });
  } catch (e) {
    return { error: e instanceof Error ? e.message : 'Approval failed' };
  }
  revalidatePath('/discovery');
  revalidatePath(revalidate);
  return {};
}

const partFields = (d: {
  name: string;
  manufacturer: string;
  modelNumber: string;
  yearReleased: number | null;
  msrpUsd: number | null;
}) => ({
  name: d.name,
  manufacturer: d.manufacturer || null,
  modelNumber: d.modelNumber || null,
  yearReleased: d.yearReleased,
  msrpCents: usdToCents(d.msrpUsd),
  isActive: true,
});

export async function approveMotherboard(
  itemId: string,
  data: ApproveMotherboardFormData,
): Promise<{ error?: string }> {
  return approvePart(itemId, 'motherboard', data.name, 'motherboard', '/motherboards', () => ({
    ...partFields(data),
    partType: 'motherboard',
    motherboard: {
      create: {
        socket: data.socket || null,
        formFactor: data.formFactor || null,
        ddrGeneration: data.ddrGeneration || null,
        memorySlots: data.memorySlots,
        hasWifi: data.hasWifi,
        m2Slots: data.m2Slots,
        m2PcieGen: data.m2PcieGen,
        chipset: data.chipset || null,
        maxMemoryGb: data.maxMemoryGb,
        sataPorts: data.sataPorts,
        pcieX16Slots: data.pcieX16Slots,
        pcieGeneration: data.pcieGeneration,
        hasBluetooth: data.hasBluetooth,
        usbTypeACount: data.usbTypeACount,
        usbTypeCCount: data.usbTypeCCount,
        audioCodec: data.audioCodec || null,
        supportsEcc: data.supportsEcc,
        hasIpmi: data.hasIpmi,
        memoryChannels: data.memoryChannels,
        memoryModuleTypes: splitCommaList(data.memoryModuleTypesInput),
      },
    },
  }));
}

export async function approveCpuCooler(
  itemId: string,
  data: ApproveCpuCoolerFormData,
): Promise<{ error?: string }> {
  return approvePart(itemId, 'cpucooler', data.name, 'CPU cooler', '/cpu-coolers', () => ({
    ...partFields(data),
    partType: 'cpucooler',
    cpuCooler: {
      create: {
        supportedSockets: splitCommaList(data.supportedSocketsInput),
        coolerType: data.coolerType || null,
        maxTdpWatts: data.maxTdpWatts,
        heightMm: data.heightMm,
        radiatorSizeMm: data.radiatorSizeMm,
        fanCount: data.fanCount,
        fanSizeMm: data.fanSizeMm,
        noiseDba: data.noiseDba,
        hasRgb: data.hasRgb,
      },
    },
  }));
}

export async function approveRamKit(
  itemId: string,
  data: ApproveRamKitFormData,
): Promise<{ error?: string }> {
  return approvePart(itemId, 'ramkit', data.name, 'memory kit', '/ram', async (tx) => {
    const groupId =
      data.groupId ||
      (
        await tx.ramGroup.create({
          data: {
            name: data.groupName || data.name,
            ddrGeneration: data.ddrGeneration,
            speedMhz: data.speedMhz ?? 0,
            capacityGb: data.capacityGb ?? 0,
            modules: data.modules ?? 1,
            moduleCapacityGb: data.moduleCapacityGb,
            casLatency: data.casLatency,
            voltage: data.voltage,
            isEcc: data.isEcc,
            moduleType: data.moduleType || null,
          },
        })
      ).id;
    return {
      ...partFields(data),
      partType: 'ramkit',
      ramKit: {
        create: {
          ramGroupId: groupId,
          heightMm: data.heightMm,
          hasRgb: data.hasRgb,
        },
      },
    };
  });
}

export async function approveStorageDrive(
  itemId: string,
  data: ApproveStorageDriveFormData,
): Promise<{ error?: string }> {
  return approvePart(itemId, 'storagedrive', data.name, 'drive', '/storage', async (tx) => {
    const groupId =
      data.groupId ||
      (
        await tx.storageGroup.create({
          data: {
            name: data.groupName || data.name,
            storageType: data.storageType,
            formFactor: data.formFactor,
            interface: data.interface,
            capacityGb: data.capacityGb ?? 0,
            readSpeedMbps: data.readSpeedMbps,
            writeSpeedMbps: data.writeSpeedMbps,
            hasDramCache: data.hasDramCache,
            enduranceTbw: data.enduranceTbw,
            rpm: data.rpm,
          },
        })
      ).id;
    return {
      ...partFields(data),
      partType: 'storagedrive',
      storageDrive: { create: { storageGroupId: groupId } },
    };
  });
}

export async function approvePsu(
  itemId: string,
  data: ApprovePsuFormData,
): Promise<{ error?: string }> {
  return approvePart(itemId, 'psu', data.name, 'power supply', '/psus', async (tx) => {
    const groupId =
      data.groupId ||
      (
        await tx.psuGroup.create({
          data: {
            name: data.groupName || data.name,
            wattage: data.wattage ?? 0,
            formFactor: data.formFactor,
            efficiencyRating: data.efficiencyRating,
            modular: data.modular || null,
            isFanless: data.isFanless,
            fanSizeMm: data.fanSizeMm,
            pcie8pinConnectors: data.pcie8pinConnectors,
            pcie12pinConnectors: data.pcie12pinConnectors,
            pcie16pinConnectors: data.pcie16pinConnectors,
            epsConnectors: data.epsConnectors,
          },
        })
      ).id;
    return {
      ...partFields(data),
      partType: 'psu',
      psu: { create: { psuGroupId: groupId, depthMm: data.depthMm } },
    };
  });
}

export async function approveCase(
  itemId: string,
  data: ApproveCaseFormData,
): Promise<{ error?: string }> {
  return approvePart(itemId, 'case', data.name, 'case', '/cases', () => ({
    ...partFields(data),
    partType: 'case',
    pcCase: {
      create: {
        supportedMoboFormFactors: splitCommaList(data.supportedMoboFormFactorsInput),
        size: data.size,
        maxGpuLengthMm: data.maxGpuLengthMm ?? 0,
        maxCoolerHeightMm: data.maxCoolerHeightMm ?? 0,
        maxRadiatorFrontMm: data.maxRadiatorFrontMm,
        maxRadiatorTopMm: data.maxRadiatorTopMm,
        maxPsuLengthMm: data.maxPsuLengthMm,
        includedFanCount: data.includedFanCount,
        chamberCount: data.chamberCount,
        frontPanelMesh: data.frontPanelMesh,
        color: data.color || null,
        driveBays35: data.driveBays35,
        driveBays25: data.driveBays25,
        maxFanSlots: data.maxFanSlots,
        hasGlassPanel: data.hasGlassPanel,
        weightKg: data.weightKg,
        lengthMm: data.lengthMm,
        widthMm: data.widthMm,
        heightMm: data.heightMm,
        usbFrontTypeA: data.usbFrontTypeA,
        usbFrontTypeC: data.usbFrontTypeC,
      },
    },
  }));
}

export async function approveFan(
  itemId: string,
  data: ApproveFanFormData,
): Promise<{ error?: string }> {
  return approvePart(itemId, 'fan', data.name, 'fan', '/fans', () => ({
    ...partFields(data),
    partType: 'fan',
    fan: {
      create: {
        sizeMm: data.sizeMm ?? 0,
        maxRpm: data.maxRpm,
        airflowCfm: data.airflowCfm,
        noiseDba: data.noiseDba,
        isPwm: data.isPwm,
        hasRgb: data.hasRgb,
        bearingType: data.bearingType || null,
        isStaticPressure: data.isStaticPressure,
        packCount: data.packCount,
      },
    },
  }));
}

/** AI models don't go through approvePart: ai_models is a standalone catalog,
 * not a pc_parts subtype, so there is no part row and the audit link lands on
 * createdAiModelId. `slug` is unique alongside `name`, so both are collision
 * checks. */
export async function approveAiModel(
  itemId: string,
  data: ApproveAiModelFormData,
): Promise<{ error?: string }> {
  try {
    await db.$transaction(async (tx) => {
      const existing = await tx.aiModel.findFirst({
        where: {
          OR: [
            { name: { equals: data.name, mode: 'insensitive' } },
            { slug: data.slug },
          ],
        },
        select: { name: true },
      });
      if (existing) {
        throw new Error(
          `An AI model matching "${existing.name}" already exists — use Mark duplicate instead`,
        );
      }
      const model = await tx.aiModel.create({
        data: {
          name: data.name,
          slug: data.slug,
          family: data.family,
          paramsBillions: data.paramsBillions,
          contextLength: data.contextLength,
          developer: data.developer || null,
          license: data.license || null,
          huggingfaceId: data.huggingfaceId || null,
          websiteUrl: data.websiteUrl || null,
          notes: data.notes || null,
        },
      });
      await markApproved(tx, itemId, { createdAiModelId: model.id });
    });
  } catch (e) {
    return { error: e instanceof Error ? e.message : 'Approval failed' };
  }
  revalidatePath('/discovery');
  revalidatePath('/ai-models');
  return {};
}

export async function rejectItem(itemId: string): Promise<{ error?: string }> {
  await db.discoveredItem.updateMany({
    where: { id: itemId, reviewStatus: 'pending' },
    data: { reviewStatus: 'rejected', reviewedAt: new Date() },
  });
  revalidatePath('/discovery');
  return {};
}

export async function markDuplicate(itemId: string): Promise<{ error?: string }> {
  await db.discoveredItem.updateMany({
    where: { id: itemId, reviewStatus: 'pending' },
    data: { reviewStatus: 'duplicate', reviewedAt: new Date() },
  });
  revalidatePath('/discovery');
  return {};
}
