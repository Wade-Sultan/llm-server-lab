'use client';

import { useEffect, useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import type { ColumnDef } from '@tanstack/react-table';
import type { DiscoveredItem } from '@prisma/client';
import { Copy, ExternalLink, Search, X } from 'lucide-react';
import { DataTable } from '@/components/data-table';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { formatDate } from '@/lib/utils';
import {
  markDuplicate,
  rejectItem,
  triggerDiscovery,
  type DiscoveryCategory,
} from './actions';
import {
  ApproveCpuForm,
  ApproveGpuChipsetForm,
  ApproveGpuVariantForm,
  type ChipsetOption,
} from './approve-forms';

// DiscoveryRun with the Decimal cost pre-serialized to a number in page.tsx.
export type SerializedRun = {
  id: string;
  runType: string;
  status: string;
  pipelineVersion: string;
  modelName: string;
  sourcesChecked: number;
  itemsFound: number;
  itemsNew: number;
  errorDetail: string | null;
  totalCostUsd: number | null;
  tokensIn: number | null;
  tokensOut: number | null;
  startedAt: Date;
  finishedAt: Date | null;
};

type Provenance = { source_url?: string; snippet?: string };
type Confidence = { agreement?: number; n_sources?: number; values?: Record<string, unknown> };

const CATEGORY_OPTIONS: { value: DiscoveryCategory; label: string }[] = [
  { value: 'cpu', label: 'CPU' },
  { value: 'gpu_chipset', label: 'GPU Chipset' },
  { value: 'gpu_variant', label: 'GPU Variant' },
];

function asRecord(v: unknown): Record<string, unknown> {
  return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

function displayValue(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  return JSON.stringify(v);
}

function hostname(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return url;
  }
}

function TriggerCard() {
  const router = useRouter();
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState<DiscoveryCategory>('cpu');
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  function submit() {
    if (query.trim().length < 3) {
      setError('Query must be at least 3 characters');
      return;
    }
    setError(null);
    startTransition(async () => {
      const res = await triggerDiscovery(query.trim(), category);
      if (res.error) setError(res.error);
      else {
        setQuery('');
        router.refresh();
      }
    });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Search for a new part</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <div className="flex gap-2">
          <Input
            placeholder='e.g. "AMD Ryzen 7 9800X3D"'
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submit()}
          />
          <Select value={category} onValueChange={(v) => setCategory(v as DiscoveryCategory)}>
            <SelectTrigger className="w-44">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {CATEGORY_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button onClick={submit} disabled={isPending}>
            <Search className="h-4 w-4 mr-2" />
            {isPending ? 'Starting...' : 'Search'}
          </Button>
        </div>
        {error && <p className="text-sm text-destructive">{error}</p>}
      </CardContent>
    </Card>
  );
}

function RunStatusBadge({ run }: { run: SerializedRun }) {
  if (run.status === 'running') {
    return (
      <Badge variant="secondary">
        <span className="mr-1.5 inline-block h-1.5 w-1.5 rounded-full bg-current animate-pulse" />
        running
      </Badge>
    );
  }
  if (run.status === 'error') {
    return (
      <Badge variant="destructive" title={run.errorDetail ?? undefined}>
        error
      </Badge>
    );
  }
  return <Badge>completed</Badge>;
}

function RunsPanel({ runs }: { runs: SerializedRun[] }) {
  if (runs.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Recent runs</CardTitle>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Status</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Model</TableHead>
              <TableHead>Found / New</TableHead>
              <TableHead>Cost</TableHead>
              <TableHead>Started</TableHead>
              <TableHead>Detail</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {runs.map((run) => (
              <TableRow key={run.id}>
                <TableCell><RunStatusBadge run={run} /></TableCell>
                <TableCell className="text-sm">{run.runType}</TableCell>
                <TableCell className="text-sm text-muted-foreground">{run.modelName}</TableCell>
                <TableCell className="text-sm">
                  {run.itemsFound} / {run.itemsNew}
                </TableCell>
                <TableCell className="text-sm">
                  {run.totalCostUsd != null ? `$${run.totalCostUsd.toFixed(4)}` : '—'}
                </TableCell>
                <TableCell className="text-sm">{formatDate(run.startedAt)}</TableCell>
                <TableCell
                  className="text-xs text-muted-foreground max-w-64 truncate"
                  title={run.errorDetail ?? undefined}
                >
                  {run.errorDetail ?? ''}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

function FieldTable({ item }: { item: DiscoveredItem }) {
  const fields = asRecord(item.extractedFields);
  const provenance = asRecord(item.fieldProvenance);
  const confidence = asRecord(item.extractionConfidence);

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-40">Field</TableHead>
          <TableHead>Value</TableHead>
          <TableHead>Source &amp; snippet</TableHead>
          <TableHead className="w-28">Agreement</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {Object.entries(fields).map(([field, value]) => {
          const prov = asRecord(provenance[field]) as Provenance;
          const conf = asRecord(confidence[field]) as Confidence;
          const disagrees = conf.values != null;
          return (
            <TableRow key={field} className={disagrees ? 'bg-amber-500/5' : undefined}>
              <TableCell className="font-mono text-xs">{field}</TableCell>
              <TableCell className="text-sm">{displayValue(value)}</TableCell>
              <TableCell>
                {prov.source_url && (
                  <a
                    href={prov.source_url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-xs text-primary hover:underline inline-flex items-center gap-1"
                  >
                    {hostname(prov.source_url)}
                    <ExternalLink className="h-3 w-3" />
                  </a>
                )}
                {prov.snippet && (
                  <p
                    className="text-xs text-muted-foreground line-clamp-2"
                    title={prov.snippet}
                  >
                    &ldquo;{prov.snippet}&rdquo;
                  </p>
                )}
              </TableCell>
              <TableCell>
                {conf.agreement != null &&
                  (disagrees ? (
                    <div className="space-y-1">
                      <Badge variant="outline" className="border-amber-500 text-amber-600">
                        {Math.round(conf.agreement * 100)}% · {conf.n_sources} src
                      </Badge>
                      <div className="text-xs text-muted-foreground space-y-0.5">
                        {Object.entries(conf.values ?? {}).map(([url, v]) => (
                          <div key={url} title={url}>
                            {hostname(url)}: {displayValue(v)}
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <span className="text-xs text-muted-foreground">
                      {conf.n_sources}/{conf.n_sources}
                    </span>
                  ))}
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

function ReviewDialog({
  item,
  chipsets,
  matchedNames,
  onClose,
}: {
  item: DiscoveredItem;
  chipsets: ChipsetOption[];
  matchedNames: Record<string, string>;
  onClose: () => void;
}) {
  const router = useRouter();
  const [step, setStep] = useState<'review' | 'approve'>('review');
  const [confirmReject, setConfirmReject] = useState(false);
  const [, startTransition] = useTransition();

  const fields = asRecord(item.extractedFields);
  const matchedId = item.matchedPartId ?? item.matchedChipsetId;
  const matchedName = matchedId ? matchedNames[matchedId] : null;
  const validationErrors = Array.isArray(item.validationErrors)
    ? (item.validationErrors as { field?: string; rule?: string; detail?: string }[])
    : [];

  const handleSuccess = () => {
    onClose();
    router.refresh();
  };

  const act = (action: (id: string) => Promise<{ error?: string }>) => {
    startTransition(async () => {
      await action(item.id);
      handleSuccess();
    });
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-4xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {step === 'approve' && (
              <Button variant="ghost" size="sm" onClick={() => setStep('review')}>
                ←
              </Button>
            )}
            {displayValue(fields.name) !== '—' ? displayValue(fields.name) : item.nameNormalized}
            <Badge variant="outline">{item.category}</Badge>
            <Badge variant={item.validationStatus === 'passed' ? 'default' : 'destructive'}>
              {item.validationStatus}
            </Badge>
          </DialogTitle>
        </DialogHeader>

        {step === 'review' ? (
          <div className="space-y-4">
            {matchedName && (
              <p className="text-sm">
                <Badge variant="outline" className="border-amber-500 text-amber-600 mr-2">
                  Likely duplicate
                </Badge>
                Matched <span className="font-medium">{matchedName}</span> ({item.matchMethod}
                {item.matchScore != null ? ` · ${item.matchScore.toFixed(2)}` : ''})
              </p>
            )}

            {validationErrors.length > 0 && (
              <div className="rounded-md border border-destructive/50 bg-destructive/5 p-3 space-y-1">
                <p className="text-sm font-medium text-destructive">Validation errors</p>
                {validationErrors.map((e, i) => (
                  <p key={i} className="text-xs text-destructive">
                    <span className="font-mono">{e.field}</span> ({e.rule}): {e.detail}
                  </p>
                ))}
              </div>
            )}

            <div className="flex flex-wrap gap-3">
              {item.sourceUrls.map((url) => (
                <a
                  key={url}
                  href={url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs text-primary hover:underline inline-flex items-center gap-1"
                >
                  {hostname(url)}
                  <ExternalLink className="h-3 w-3" />
                </a>
              ))}
            </div>

            <FieldTable item={item} />

            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" onClick={() => act(markDuplicate)}>
                <Copy className="h-3.5 w-3.5 mr-1.5" />
                Mark duplicate
              </Button>
              <Button variant="outline" className="text-destructive" onClick={() => setConfirmReject(true)}>
                <X className="h-3.5 w-3.5 mr-1.5" />
                Reject
              </Button>
              <Button onClick={() => setStep('approve')}>Approve…</Button>
            </div>
          </div>
        ) : (
          <>
            {item.category === 'cpu' && (
              <ApproveCpuForm itemId={item.id} extractedFields={fields} onSuccess={handleSuccess} />
            )}
            {item.category === 'gpu_chipset' && (
              <ApproveGpuChipsetForm itemId={item.id} extractedFields={fields} onSuccess={handleSuccess} />
            )}
            {item.category === 'gpu_variant' && (
              <ApproveGpuVariantForm
                itemId={item.id}
                extractedFields={fields}
                chipsets={chipsets}
                onSuccess={handleSuccess}
              />
            )}
          </>
        )}

        <AlertDialog open={confirmReject} onOpenChange={setConfirmReject}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Reject this item?</AlertDialogTitle>
              <AlertDialogDescription>
                It will leave the queue without creating a catalog entry.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction onClick={() => act(rejectItem)}>Reject</AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </DialogContent>
    </Dialog>
  );
}

export function DiscoveryClient({
  items,
  runs,
  chipsets,
  matchedNames,
}: {
  items: DiscoveredItem[];
  runs: SerializedRun[];
  chipsets: ChipsetOption[];
  matchedNames: Record<string, string>;
}) {
  const router = useRouter();
  const [reviewing, setReviewing] = useState<DiscoveredItem | null>(null);
  const [, startTransition] = useTransition();

  const anyRunning = runs.some((r) => r.status === 'running');
  useEffect(() => {
    if (!anyRunning) return;
    const t = setInterval(() => router.refresh(), 4000);
    return () => clearInterval(t);
  }, [anyRunning, router]);

  const quickAct = (id: string, action: (id: string) => Promise<{ error?: string }>) => {
    startTransition(async () => {
      await action(id);
      router.refresh();
    });
  };

  const columns: ColumnDef<DiscoveredItem>[] = [
    {
      id: 'name',
      accessorKey: 'nameNormalized',
      header: 'Name',
      enableSorting: true,
      cell: ({ row }) => (
        <div>
          <p className="font-medium">{row.original.nameNormalized}</p>
          {row.original.modelNumber && (
            <p className="text-xs text-muted-foreground">{row.original.modelNumber}</p>
          )}
        </div>
      ),
    },
    {
      accessorKey: 'category',
      header: 'Category',
      cell: ({ getValue }) => <Badge variant="outline">{getValue<string>()}</Badge>,
    },
    {
      accessorKey: 'validationStatus',
      header: 'Validation',
      cell: ({ row }) => {
        const failed = row.original.validationStatus === 'failed';
        const count = Array.isArray(row.original.validationErrors)
          ? row.original.validationErrors.length
          : 0;
        return (
          <Badge variant={failed ? 'destructive' : 'default'}>
            {failed ? `${count} error${count === 1 ? '' : 's'}` : 'passed'}
          </Badge>
        );
      },
    },
    {
      id: 'match',
      header: 'Match',
      cell: ({ row }) => {
        const id = row.original.matchedPartId ?? row.original.matchedChipsetId;
        if (!id) return <span className="text-muted-foreground">—</span>;
        return (
          <Badge variant="outline" className="border-amber-500 text-amber-600">
            Likely dup: {matchedNames[id] ?? '?'} ({row.original.matchMethod})
          </Badge>
        );
      },
    },
    {
      id: 'sources',
      header: 'Sources',
      cell: ({ row }) => row.original.sourceUrls.length,
    },
    {
      id: 'found',
      accessorKey: 'createdAt',
      header: 'Found',
      enableSorting: true,
      cell: ({ getValue }) => formatDate(getValue<Date>()),
    },
    {
      id: 'actions',
      header: '',
      cell: ({ row }) => (
        <div className="flex items-center gap-1">
          <Button size="sm" onClick={() => setReviewing(row.original)}>
            Review
          </Button>
          <Button
            variant="ghost"
            size="sm"
            title="Mark duplicate"
            onClick={() => quickAct(row.original.id, markDuplicate)}
          >
            <Copy className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="text-destructive hover:text-destructive"
            title="Reject"
            onClick={() => quickAct(row.original.id, rejectItem)}
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
      ),
    },
  ];

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Discovery</h1>
        <p className="text-muted-foreground text-sm mt-1">
          {items.length} pending item{items.length === 1 ? '' : 's'}
        </p>
      </div>

      <TriggerCard />
      <RunsPanel runs={runs} />

      <DataTable columns={columns} data={items} filterPlaceholder="Filter queue..." />

      {reviewing && (
        <ReviewDialog
          item={reviewing}
          chipsets={chipsets}
          matchedNames={matchedNames}
          onClose={() => setReviewing(null)}
        />
      )}
    </div>
  );
}
