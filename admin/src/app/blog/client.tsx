'use client';

import { useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import type { ColumnDef } from '@tanstack/react-table';
import type { BlogPost } from '@prisma/client';
import { Pencil, Trash2, Upload, X } from 'lucide-react';
import { DataTable } from '@/components/data-table';
import { MarkdownEditor } from '@/components/markdown-editor';
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { formatDate, joinCommaList } from '@/lib/utils';
import { uploadImage } from '@/lib/upload-client';
import { createBlogPost, updateBlogPost, deleteBlogPost, type BlogPostFormData } from './actions';

const STATUSES = ['draft', 'published'] as const;

const schema = z.object({
  title: z.string().min(1, 'Title is required'),
  slug: z.string(),
  excerpt: z.string(),
  contentMarkdown: z.string().min(1, 'Post body is required'),
  coverImageUrl: z.string(),
  coverImageAlt: z.string(),
  authorName: z.string(),
  tagsInput: z.string(),
  status: z.string().min(1),
  isFeatured: z.boolean(),
});

function postDefaults(item: BlogPost | null): BlogPostFormData {
  if (!item) {
    return {
      title: '', slug: '', excerpt: '', contentMarkdown: '', coverImageUrl: '',
      coverImageAlt: '', authorName: '', tagsInput: '', status: 'draft', isFeatured: false,
    };
  }
  return {
    title: item.title,
    slug: item.slug,
    excerpt: item.excerpt ?? '',
    contentMarkdown: item.contentMarkdown,
    coverImageUrl: item.coverImageUrl ?? '',
    coverImageAlt: item.coverImageAlt ?? '',
    authorName: item.authorName ?? '',
    tagsInput: joinCommaList(item.tags),
    status: item.status,
    isFeatured: item.isFeatured,
  };
}

/** Cover image picker: uploads to GCS and stores the returned URL. */
function CoverImageField({
  value, onChange,
}: {
  value: string;
  onChange: (url: string) => void;
}) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleFile(file: File) {
    setError(null);
    setUploading(true);
    try {
      onChange(await uploadImage(file));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Upload failed');
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <Input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="Upload a file, or paste an image URL"
        />
        <Button type="button" variant="outline" disabled={uploading} onClick={() => fileInput.current?.click()}>
          <Upload className="h-3.5 w-3.5" />
          {uploading ? 'Uploading…' : 'Upload'}
        </Button>
        {value && (
          <Button type="button" variant="ghost" size="icon" title="Remove cover image" onClick={() => onChange('')}>
            <X className="h-3.5 w-3.5" />
          </Button>
        )}
      </div>
      <input
        ref={fileInput}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = '';
          if (file) void handleFile(file);
        }}
      />
      {value && (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={value} alt="Cover preview" className="h-32 rounded-md border object-cover" />
      )}
      {error && <p className="text-sm text-destructive">{error}</p>}
    </div>
  );
}

function BlogPostForm({ item, onSuccess }: { item: BlogPost | null; onSuccess: () => void }) {
  const form = useForm<BlogPostFormData>({
    resolver: zodResolver(schema),
    defaultValues: postDefaults(item),
  });
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(data: BlogPostFormData) {
    setError(null);
    try {
      if (item) { await updateBlogPost(item.id, data); } else { await createBlogPost(data); }
      onSuccess();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'An error occurred');
    }
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          {([
            ['title', 'Title *'],
            ['slug', 'Slug (blank = from title)'],
            ['authorName', 'Author'],
            ['tagsInput', 'Tags (comma-separated)'],
          ] as [keyof BlogPostFormData, string][]).map(([name, label]) => (
            <FormField key={name} control={form.control} name={name}
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{label}</FormLabel>
                  <FormControl><Input {...field} value={field.value as string ?? ''} /></FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          ))}
        </div>

        <FormField control={form.control} name="excerpt"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Excerpt (shown on the index page and used as the SEO description)</FormLabel>
              <FormControl><Textarea rows={2} {...field} /></FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField control={form.control} name="coverImageUrl"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Cover Image</FormLabel>
              <FormControl><CoverImageField value={field.value} onChange={field.onChange} /></FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        {form.watch('coverImageUrl') && (
          <FormField control={form.control} name="coverImageAlt"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Cover Image Alt Text</FormLabel>
                <FormControl><Input {...field} placeholder="Describe the image for screen readers" /></FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
        )}

        <FormField control={form.control} name="contentMarkdown"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Body</FormLabel>
              <FormControl><MarkdownEditor value={field.value} onChange={field.onChange} /></FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <div className="flex items-end gap-6 border-t pt-4">
          <FormField control={form.control} name="status"
            render={({ field }) => (
              <FormItem className="w-48">
                <FormLabel>Status</FormLabel>
                <Select value={field.value} onValueChange={field.onChange}>
                  <FormControl><SelectTrigger><SelectValue /></SelectTrigger></FormControl>
                  <SelectContent>
                    {STATUSES.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
                  </SelectContent>
                </Select>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField control={form.control} name="isFeatured"
            render={({ field }) => (
              <FormItem className="flex items-center gap-2 pb-2">
                <FormControl>
                  <Checkbox checked={field.value} onCheckedChange={(v) => field.onChange(v === true)} />
                </FormControl>
                <FormLabel className="!mt-0">Feature at the top of the blog index</FormLabel>
              </FormItem>
            )}
          />
        </div>

        {error && <p className="text-sm text-destructive">{error}</p>}
        <div className="flex justify-end pt-2">
          <Button type="submit" disabled={form.formState.isSubmitting}>
            {form.formState.isSubmitting ? 'Saving...' : item ? 'Update Post' : 'Create Post'}
          </Button>
        </div>
      </form>
    </Form>
  );
}

export function BlogPostsTable({ posts }: { posts: BlogPost[] }) {
  const router = useRouter();
  const [selected, setSelected] = useState<BlogPost | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deleteId, setDeleteId] = useState<string | null>(null);

  const handleSuccess = () => { setDialogOpen(false); router.refresh(); };
  const handleDelete = async (id: string) => { await deleteBlogPost(id); setDeleteId(null); router.refresh(); };

  const columns: ColumnDef<BlogPost>[] = [
    { accessorKey: 'title', header: 'Title', enableSorting: true },
    {
      accessorKey: 'status', header: 'Status',
      cell: ({ row }) => (
        <div className="flex items-center gap-1.5">
          <Badge variant={row.original.status === 'published' ? 'default' : 'secondary'}>
            {row.original.status}
          </Badge>
          {row.original.isFeatured && <Badge variant="secondary">featured</Badge>}
        </div>
      ),
    },
    {
      accessorKey: 'publishedAt', header: 'Published', enableSorting: true,
      cell: ({ getValue }) => {
        const v = getValue<Date | null>();
        return v ? formatDate(v) : <span className="text-muted-foreground text-xs">—</span>;
      },
    },
    {
      accessorKey: 'tags', header: 'Tags',
      cell: ({ getValue }) => {
        const tags = getValue<string[]>();
        return tags.length
          ? <span className="text-xs text-muted-foreground">{tags.join(', ')}</span>
          : <span className="text-muted-foreground text-xs">—</span>;
      },
    },
    {
      accessorKey: 'readingMinutes', header: 'Read',
      cell: ({ getValue }) => {
        const v = getValue<number | null>();
        return v ? <span className="text-xs text-muted-foreground">{v} min</span> : '—';
      },
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
          <h1 className="text-2xl font-bold">Blog</h1>
          <p className="text-muted-foreground text-sm mt-1">
            {posts.length} total · {posts.filter((p) => p.status === 'published').length} published
          </p>
        </div>
        <Button onClick={() => { setSelected(null); setDialogOpen(true); }}>New Post</Button>
      </div>
      <DataTable columns={columns} data={posts} filterPlaceholder="Filter posts..." filterColumn="title" />
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto">
          <DialogHeader><DialogTitle>{selected ? 'Edit Post' : 'New Post'}</DialogTitle></DialogHeader>
          {/* Remount on switch so the editor picks up the new post's body. */}
          <BlogPostForm key={selected?.id ?? 'new'} item={selected} onSuccess={handleSuccess} />
        </DialogContent>
      </Dialog>
      <AlertDialog open={!!deleteId} onOpenChange={(open) => !open && setDeleteId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Post?</AlertDialogTitle>
            <AlertDialogDescription>
              This cannot be undone. Images already uploaded to the media bucket are not removed.
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
