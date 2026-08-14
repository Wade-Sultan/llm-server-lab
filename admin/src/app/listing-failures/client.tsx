'use client';

import { useMemo, useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import type { ColumnDef } from '@tanstack/react-table';
import { Check, RotateCcw } from 'lucide-react';
import { DataTable } from '@/components/data-table';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { formatDate } from '@/lib/utils';
import { resolveFailure, reopenFailure } from './actions';

export interface FailureRow {
  partId: string;
  partName: string;
  partType: string;
  partIsActive: boolean;
  reason: string;
  detail: string | null;
  occurrences: number;
  firstSeenAt: string;
  lastSeenAt: string;
  notifiedAt: string | null;
  resolvedAt: string | null;
}

// Stored enums, spelled for a human. Matches commerce's reasonLabel and
// backend/app/models/listing_failure.py; an unrecognized value passes through
// rather than being dropped, so a reason added later looks odd here instead of
// silently vanishing.
const REASON_LABELS: Record<string, string> = {
  no_active_listing: 'No active listing',
  lookup_error: 'Lookup failed',
};

function reasonLabel(reason: string) {
  return REASON_LABELS[reason] ?? reason;
}

function ReasonBadge({ reason }: { reason: string }) {
  // A coverage gap is a content problem (add a listing); a lookup error is an
  // operational one. Different colours because they go to different fixes.
  const variant = reason === 'lookup_error' ? 'destructive' : 'secondary';
  return <Badge variant={variant}>{reasonLabel(reason)}</Badge>;
}

function RowActions({ row }: { row: FailureRow }) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();

  const open = row.resolvedAt === null;

  return (
    <Button
      variant="ghost"
      size="sm"
      disabled={pending}
      onClick={() =>
        startTransition(async () => {
          if (open) {
            await resolveFailure(row.partId);
          } else {
            await reopenFailure(row.partId);
          }
          router.refresh();
        })
      }
    >
      {open ? (
        <>
          <Check className="mr-1 h-4 w-4" />
          Resolve
        </>
      ) : (
        <>
          <RotateCcw className="mr-1 h-4 w-4" />
          Reopen
        </>
      )}
    </Button>
  );
}

export function ListingFailuresTable({ rows }: { rows: FailureRow[] }) {
  const [showResolved, setShowResolved] = useState(false);

  const visible = useMemo(
    () => (showResolved ? rows : rows.filter((r) => r.resolvedAt === null)),
    [rows, showResolved],
  );

  const openCount = useMemo(() => rows.filter((r) => r.resolvedAt === null).length, [rows]);

  const columns = useMemo<ColumnDef<FailureRow>[]>(
    () => [
      {
        // Named "name" so DataTable's default filterColumn finds it — the
        // part name is the only thing anyone searches this table by.
        accessorKey: 'partName',
        id: 'name',
        header: 'Part',
        cell: ({ row }) => (
          <div className="flex flex-col">
            <span className="font-medium">{row.original.partName}</span>
            <span className="text-xs text-muted-foreground">
              {row.original.partType}
              {!row.original.partIsActive && ' · inactive'}
            </span>
          </div>
        ),
      },
      {
        accessorKey: 'reason',
        header: 'Reason',
        cell: ({ row }) => (
          <div className="flex flex-col gap-1">
            <ReasonBadge reason={row.original.reason} />
            {row.original.detail && (
              <span
                className="max-w-xs truncate text-xs text-muted-foreground"
                title={row.original.detail}
              >
                {row.original.detail}
              </span>
            )}
          </div>
        ),
      },
      {
        accessorKey: 'occurrences',
        header: 'Hits',
        cell: ({ row }) => <span className="tabular-nums">{row.original.occurrences}</span>,
      },
      {
        accessorKey: 'firstSeenAt',
        header: 'First seen',
        cell: ({ row }) => formatDate(row.original.firstSeenAt),
      },
      {
        accessorKey: 'lastSeenAt',
        header: 'Last seen',
        cell: ({ row }) => formatDate(row.original.lastSeenAt),
      },
      {
        id: 'status',
        header: 'Status',
        cell: ({ row }) => {
          if (row.original.resolvedAt) {
            return <Badge variant="outline">Resolved {formatDate(row.original.resolvedAt)}</Badge>;
          }
          // Whether it reached the digest, so a row nobody was told about is
          // visibly different from one that was already reported.
          return row.original.notifiedAt ? (
            <span className="text-xs text-muted-foreground">
              Reported {formatDate(row.original.notifiedAt)}
            </span>
          ) : (
            <span className="text-xs text-muted-foreground">Not yet reported</span>
          );
        },
      },
      {
        id: 'actions',
        cell: ({ row }) => <RowActions row={row.original} />,
      },
    ],
    [],
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Listing failures</h1>
          <p className="text-sm text-muted-foreground">
            {openCount === 0
              ? 'Every part the listings API was asked for had something to buy.'
              : `${openCount} part${openCount === 1 ? '' : 's'} the listings API could not produce a listing for. They still get recommended; they just have nothing to buy.`}
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => setShowResolved((v) => !v)}>
          {showResolved ? 'Hide resolved' : 'Show resolved'}
        </Button>
      </div>

      <DataTable columns={columns} data={visible} filterPlaceholder="Filter by part name..." />
    </div>
  );
}
