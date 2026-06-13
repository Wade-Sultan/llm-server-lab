'use server';

import { revalidatePath } from 'next/cache';
import { db } from '@/lib/prisma';

export interface ReferenceBuildFormData {
  buildKey: string;
  label: string;
  description: string;
  totalApprox: number | null;
  isActive: boolean;
}

export async function createReferenceBuild(data: ReferenceBuildFormData) {
  await db.referenceBuild.create({
    data: {
      buildKey: data.buildKey,
      label: data.label,
      description: data.description || null,
      totalApprox: data.totalApprox,
      isActive: data.isActive,
    },
  });
  revalidatePath('/reference-builds');
}

export async function updateReferenceBuild(id: string, data: ReferenceBuildFormData) {
  await db.referenceBuild.update({
    where: { id },
    data: {
      buildKey: data.buildKey,
      label: data.label,
      description: data.description || null,
      totalApprox: data.totalApprox,
      isActive: data.isActive,
    },
  });
  revalidatePath('/reference-builds');
}

export async function deleteReferenceBuild(id: string) {
  await db.referenceBuild.delete({ where: { id } });
  revalidatePath('/reference-builds');
}
