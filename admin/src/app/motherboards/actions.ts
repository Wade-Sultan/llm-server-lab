'use server';

import { revalidatePath } from 'next/cache';
import { db } from '@/lib/prisma';
import { usdToCents } from '@/lib/utils';
import { upsertAmazonAsin } from '@/lib/listings';

export interface MotherboardFormData {
  name: string;
  manufacturer: string;
  modelNumber: string;
  yearReleased: number | null;
  isActive: boolean;
  streetPriceUsd: number | null;
  asin: string;
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
}

const partData = (d: MotherboardFormData) => ({
  name: d.name,
  manufacturer: d.manufacturer || null,
  modelNumber: d.modelNumber || null,
  yearReleased: d.yearReleased,
  isActive: d.isActive,
  streetPriceCents: usdToCents(d.streetPriceUsd),
});

const specificData = (d: MotherboardFormData) => ({
  socket: d.socket || null,
  formFactor: d.formFactor || null,
  ddrGeneration: d.ddrGeneration || null,
  memorySlots: d.memorySlots,
  hasWifi: d.hasWifi,
  m2Slots: d.m2Slots,
  m2PcieGen: d.m2PcieGen,
  chipset: d.chipset || null,
  maxMemoryGb: d.maxMemoryGb,
  sataPorts: d.sataPorts,
  pcieX16Slots: d.pcieX16Slots,
  pcieGeneration: d.pcieGeneration,
  hasBluetooth: d.hasBluetooth,
  usbTypeACount: d.usbTypeACount,
  usbTypeCCount: d.usbTypeCCount,
  audioCodec: d.audioCodec || null,
});

export async function createMotherboard(data: MotherboardFormData) {
  const created = await db.pcPart.create({
    data: { ...partData(data), partType: 'motherboard', motherboard: { create: specificData(data) } },
  });
  await upsertAmazonAsin(created.id, data.asin);
  revalidatePath('/motherboards');
}

export async function updateMotherboard(id: string, data: MotherboardFormData) {
  await db.$transaction([
    db.pcPart.update({ where: { id }, data: partData(data) }),
    db.motherboard.update({ where: { id }, data: specificData(data) }),
  ]);
  await upsertAmazonAsin(id, data.asin);
  revalidatePath('/motherboards');
}

export async function deleteMotherboard(id: string) {
  await db.pcPart.delete({ where: { id } });
  revalidatePath('/motherboards');
}
