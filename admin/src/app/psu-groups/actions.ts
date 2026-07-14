'use server';

import { revalidatePath } from 'next/cache';
import { db } from '@/lib/prisma';
import { usdToCents } from '@/lib/utils';

export interface PsuGroupFormData {
  name: string;
  streetPriceUsd: number | null;
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
}

function toData(d: PsuGroupFormData) {
  return {
    name: d.name,
    streetPriceCents: usdToCents(d.streetPriceUsd),
    wattage: d.wattage ?? 0,
    formFactor: d.formFactor,
    efficiencyRating: d.efficiencyRating,
    modular: d.modular || null,
    isFanless: d.isFanless,
    fanSizeMm: d.fanSizeMm,
    pcie8pinConnectors: d.pcie8pinConnectors,
    pcie12pinConnectors: d.pcie12pinConnectors,
    pcie16pinConnectors: d.pcie16pinConnectors,
    epsConnectors: d.epsConnectors,
  };
}

export async function createPsuGroup(data: PsuGroupFormData) {
  await db.psuGroup.create({ data: toData(data) });
  revalidatePath('/psu-groups');
}

export async function updatePsuGroup(id: string, data: PsuGroupFormData) {
  await db.psuGroup.update({ where: { id }, data: toData(data) });
  revalidatePath('/psu-groups');
}

export async function deletePsuGroup(id: string) {
  await db.psuGroup.delete({ where: { id } });
  revalidatePath('/psu-groups');
}
