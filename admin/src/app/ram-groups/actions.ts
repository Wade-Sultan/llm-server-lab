'use server';

import { revalidatePath } from 'next/cache';
import { db } from '@/lib/prisma';

export interface RamGroupFormData {
  name: string;
  ddrGeneration: string;
  speedMhz: number | null;
  capacityGb: number | null;
  modules: number | null;
  moduleCapacityGb: number | null;
  casLatency: number | null;
  voltage: number | null;
  isEcc: boolean;
}

function toData(d: RamGroupFormData) {
  return {
    name: d.name,
    ddrGeneration: d.ddrGeneration,
    speedMhz: d.speedMhz ?? 0,
    capacityGb: d.capacityGb ?? 0,
    modules: d.modules ?? 0,
    moduleCapacityGb: d.moduleCapacityGb,
    casLatency: d.casLatency,
    voltage: d.voltage,
    isEcc: d.isEcc,
  };
}

export async function createRamGroup(data: RamGroupFormData) {
  await db.ramGroup.create({ data: toData(data) });
  revalidatePath('/ram-groups');
}

export async function updateRamGroup(id: string, data: RamGroupFormData) {
  await db.ramGroup.update({ where: { id }, data: toData(data) });
  revalidatePath('/ram-groups');
}

export async function deleteRamGroup(id: string) {
  await db.ramGroup.delete({ where: { id } });
  revalidatePath('/ram-groups');
}
