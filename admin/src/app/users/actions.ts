'use server';

import { revalidatePath } from 'next/cache';
import { db } from '@/lib/prisma';

export interface UserFormData {
  email: string;
  username: string;
  firebaseUid: string;
  isActive: boolean;
  isSuperuser: boolean;
}

export async function updateUser(id: string, data: UserFormData) {
  await db.user.update({
    where: { id },
    data: {
      email: data.email,
      username: data.username || null,
      firebaseUid: data.firebaseUid || null,
      isActive: data.isActive,
      isSuperuser: data.isSuperuser,
    },
  });
  revalidatePath('/users');
}

export async function deleteUser(id: string) {
  await db.user.delete({ where: { id } });
  revalidatePath('/users');
}
