'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import type { ColumnDef } from '@tanstack/react-table';
import type { GuideVideo } from '@prisma/client';
import { ExternalLink, Pencil, Trash2 } from 'lucide-react';
import { DataTable } from '@/components/data-table';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Checkbox } from '@/components/ui/checkbox';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { extractYouTubeId, youtubeUrlSchema } from '@/lib/utils';
import {
  createGuideVideo, updateGuideVideo, deleteGuideVideo, type GuideVideoFormData,
} from './actions';

const schema = z.object({
  title: z.string().min(1, 'Title is required'),
  description: z.string(),
  url: youtubeUrlSchema,
  isPublished: z.boolean(),
  sortOrder: z.coerce.number().int(),
});

function videoDefaults(item: GuideVideo | null): GuideVideoFormData {
  if (!item) {
    return { title: '', description: '', url: '', isPublished: true, sortOrder: 0 };
  }
  return {
    title: item.title,
    description: item.description ?? '',
    url: item.url,
    isPublished: item.isPublished,
    sortOrder: item.sortOrder,
  };
}

function GuideVideoForm({ item, onSuccess }: { item: GuideVideo | null; onSuccess: () => void }) {
  const form = useForm<GuideVideoFormData>({
    resolver: zodResolver(schema),
    defaultValues: videoDefaults(item),
  });
  const [error, setError] = useState<string | null>(null);

  // Live feedback on whether the pasted link will embed, so a typo is obvious
  // before saving rather than after looking at the public page.
  const url = form.watch('url');
  const detectedId = url ? extractYouTubeId(url) : null;

  async function onSubmit(data: GuideVideoFormData) {
    setError(null);
    try {
      if (item) { await updateGuideVideo(item.id, data); } else { await createGuideVideo(data); }
      onSuccess();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'An error occurred');
    }
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
        <FormField control={form.control} name="title"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Title *</FormLabel>
              <FormControl><Input {...field} /></FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField control={form.control} name="url"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Video URL *</FormLabel>
              <FormControl>
                <Input {...field} placeholder="https://www.youtube.com/watch?v=..." />
              </FormControl>
              {url && (
                <p className="text-xs text-muted-foreground">
                  {detectedId
                    ? `YouTube video ${detectedId} — will play inline on the guides page.`
                    : 'Not a recognised YouTube link — this will render as an outbound link card instead of an embed.'}
                </p>
              )}
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField control={form.control} name="description"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Description</FormLabel>
              <FormControl><Textarea rows={2} {...field} /></FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <div className="flex items-end gap-6 border-t pt-4">
          <FormField control={form.control} name="sortOrder"
            render={({ field }) => (
              <FormItem className="w-32">
                <FormLabel>Sort Order</FormLabel>
                <FormControl>
                  <Input
                    type="number"
                    value={field.value ?? 0}
                    onChange={(e) => field.onChange(e.target.value === '' ? 0 : Number(e.target.value))}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField control={form.control} name="isPublished"
            render={({ field }) => (
              <FormItem className="flex items-center gap-2 pb-2">
                <FormControl>
                  <Checkbox checked={field.value} onCheckedChange={(v) => field.onChange(v === true)} />
                </FormControl>
                <FormLabel className="!mt-0">Show on the public guides page</FormLabel>
              </FormItem>
            )}
          />
        </div>

        {error && <p className="text-sm text-destructive">{error}</p>}
        <div className="flex justify-end pt-2">
          <Button type="submit" disabled={form.formState.isSubmitting}>
            {form.formState.isSubmitting ? 'Saving...' : item ? 'Update Video' : 'Add Video'}
          </Button>
        </div>
      </form>
    </Form>
  );
}

export function GuideVideosTable({ videos }: { videos: GuideVideo[] }) {
  const router = useRouter();
  const [selected, setSelected] = useState<GuideVideo | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deleteId, setDeleteId] = useState<string | null>(null);

  const handleSuccess = () => { setDialogOpen(false); router.refresh(); };
  const handleDelete = async (id: string) => { await deleteGuideVideo(id); setDeleteId(null); router.refresh(); };

  const columns: ColumnDef<GuideVideo>[] = [
    { accessorKey: 'sortOrder', header: '#', enableSorting: true },
    { accessorKey: 'title', header: 'Title', enableSorting: true },
    {
      accessorKey: 'youtubeVideoId', header: 'Link',
      cell: ({ row }) => (
        <a
          href={row.original.url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
        >
          {row.original.youtubeVideoId ?? 'external'}
          <ExternalLink className="h-3 w-3" />
        </a>
      ),
    },
    {
      accessorKey: 'isPublished', header: 'Status',
      cell: ({ row }) => (
        <Badge variant={row.original.isPublished ? 'default' : 'secondary'}>
          {row.original.isPublished ? 'published' : 'hidden'}
        </Badge>
      ),
    },
    {
      id: 'actions', header: '',
      cell: ({ row }) => (
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="sm" onClick={() => { setSelected(row.original); setDialogOpen(true); }}>
            <Pencil className="h-3.5 w-3.5" />
          </Button>
          <Button variant="ghost" size="sm" className="text-destructive hover:text-destructive" onClick={() => setDeleteId(row.original.id)}>
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
          <h1 className="text-2xl font-bold">Guide Videos</h1>
          <p className="text-muted-foreground text-sm mt-1">
            {videos.length} total · {videos.filter((v) => v.isPublished).length} on the public guides page
          </p>
        </div>
        <Button onClick={() => { setSelected(null); setDialogOpen(true); }}>Add Video</Button>
      </div>
      <DataTable columns={columns} data={videos} filterPlaceholder="Filter videos..." filterColumn="title" />
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
          <DialogHeader><DialogTitle>{selected ? 'Edit Video' : 'Add Video'}</DialogTitle></DialogHeader>
          <GuideVideoForm key={selected?.id ?? 'new'} item={selected} onSuccess={handleSuccess} />
        </DialogContent>
      </Dialog>
      <AlertDialog open={!!deleteId} onOpenChange={(open) => !open && setDeleteId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Video?</AlertDialogTitle>
            <AlertDialogDescription>
              This removes it from the guides page. Only the link is deleted — the video itself is on YouTube.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => deleteId && handleDelete(deleteId)}>Delete</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
