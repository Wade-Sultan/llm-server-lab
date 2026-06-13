'use client';

import { useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import type { ColumnDef } from '@tanstack/react-table';
import type { User } from '@prisma/client';
import { Pencil, Trash2 } from 'lucide-react';
import { DataTable } from '@/components/data-table';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog';
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { Checkbox } from '@/components/ui/checkbox';
import { formatDate } from '@/lib/utils';
import { updateUser, deleteUser, type UserFormData } from './actions';

const schema = z.object({
  email: z.string().email('Invalid email'),
  username: z.string(),
  firebaseUid: z.string(),
  isActive: z.boolean(),
  isSuperuser: z.boolean(),
});

function UserEditForm({ item, onSuccess }: { item: User; onSuccess: () => void }) {
  const form = useForm<UserFormData>({
    resolver: zodResolver(schema),
    defaultValues: {
      email: item.email, username: item.username ?? '',
      firebaseUid: item.firebaseUid ?? '',
      isActive: item.isActive, isSuperuser: item.isSuperuser,
    },
  });

  const [error, setError] = useState<string | null>(null);

  async function onSubmit(data: UserFormData) {
    setError(null);
    try {
      await updateUser(item.id, data);
      onSuccess();
    } catch (e) { setError(e instanceof Error ? e.message : 'An error occurred'); }
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
        <FormField control={form.control} name="email"
          render={({ field }) => (
            <FormItem><FormLabel>Email *</FormLabel>
              <FormControl><Input {...field} type="email" /></FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField control={form.control} name="username"
          render={({ field }) => (
            <FormItem><FormLabel>Username</FormLabel>
              <FormControl><Input {...field} /></FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField control={form.control} name="firebaseUid"
          render={({ field }) => (
            <FormItem><FormLabel>Firebase UID</FormLabel>
              <FormControl><Input {...field} /></FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <div className="flex gap-6">
          {([ ['isActive','Active'], ['isSuperuser','Superuser'] ] as [keyof UserFormData, string][]).map(([name, label]) => (
            <FormField key={name} control={form.control} name={name}
              render={({ field }) => (
                <FormItem className="flex items-center gap-2 space-y-0">
                  <FormControl><Checkbox checked={field.value as boolean} onCheckedChange={field.onChange} /></FormControl>
                  <FormLabel>{label}</FormLabel>
                </FormItem>
              )}
            />
          ))}
        </div>
        {error && <p className="text-sm text-destructive">{error}</p>}
        <div className="flex justify-end pt-2">
          <Button type="submit" disabled={form.formState.isSubmitting}>
            {form.formState.isSubmitting ? 'Saving...' : 'Update User'}
          </Button>
        </div>
      </form>
    </Form>
  );
}

export function UserTable({ data }: { data: User[] }) {
  const router = useRouter();
  const [selected, setSelected] = useState<User | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const [, startTransition] = useTransition();

  const handleSuccess = () => { setDialogOpen(false); router.refresh(); };
  const handleDelete = (id: string) => {
    startTransition(async () => { await deleteUser(id); setDeleteId(null); router.refresh(); });
  };

  const columns: ColumnDef<User>[] = [
    { accessorKey: 'email', header: 'Email', enableSorting: true },
    { accessorKey: 'username', header: 'Username', enableSorting: true,
      cell: ({ getValue }) => getValue<string | null>() ?? '—' },
    { accessorKey: 'createdAt', header: 'Joined', enableSorting: true,
      cell: ({ getValue }) => formatDate(getValue<Date>()) },
    { accessorKey: 'isActive', header: 'Active',
      cell: ({ getValue }) => <Badge variant={getValue<boolean>() ? 'default' : 'secondary'}>{getValue<boolean>() ? 'Active' : 'Inactive'}</Badge> },
    { accessorKey: 'isSuperuser', header: 'Superuser',
      cell: ({ getValue }) => getValue<boolean>() ? <Badge>Superuser</Badge> : null },
    { id: 'actions', header: '', cell: ({ row }) => (
      <div className="flex items-center gap-1">
        <Button variant="ghost" size="sm" onClick={() => { setSelected(row.original); setDialogOpen(true); }}><Pencil className="h-3.5 w-3.5" /></Button>
        <Button variant="ghost" size="sm" className="text-destructive hover:text-destructive" onClick={() => setDeleteId(row.original.id)}><Trash2 className="h-3.5 w-3.5" /></Button>
      </div>
    )},
  ];

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div><h1 className="text-2xl font-bold">Users</h1><p className="text-muted-foreground text-sm mt-1">{data.length} total</p></div>
      </div>
      <DataTable columns={columns} data={data} filterPlaceholder="Filter users..." filterColumn="email" />
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>Edit User</DialogTitle></DialogHeader>
          {selected && <UserEditForm item={selected} onSuccess={handleSuccess} />}
        </DialogContent>
      </Dialog>
      <AlertDialog open={!!deleteId} onOpenChange={(open) => !open && setDeleteId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader><AlertDialogTitle>Delete User?</AlertDialogTitle><AlertDialogDescription>This permanently deletes the user account. This action cannot be undone.</AlertDialogDescription></AlertDialogHeader>
          <AlertDialogFooter><AlertDialogCancel>Cancel</AlertDialogCancel><AlertDialogAction onClick={() => deleteId && handleDelete(deleteId)}>Delete</AlertDialogAction></AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
