package store

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/google/uuid"
)

// Parts the listings API could not produce a listing for.
//
// The schema lives in backend/app/alembic (migration c3e5a7b9d1f4) like every
// other table; this file is just the queries. Keyed by part_id — current state,
// not an event log. The build card fetches listings per part on every render,
// so one part with no listing is hit dozens of times a day; an append-only log
// would be almost entirely duplicates and the digest built on it unreadable.

// Reasons, matching backend/app/models/listing_failure.py.
const (
	// ReasonNoActiveListing: the part exists and gets recommended, but there
	// is nothing active to buy. A coverage gap — the fix is to add a listing.
	ReasonNoActiveListing = "no_active_listing"
	// ReasonLookupError: the query itself failed. The fix is operational, and
	// the part may well be fine.
	ReasonLookupError = "lookup_error"
)

// ListingFailure is one open (or resolved) coverage gap, joined to the part it
// is about — a bare part_id is useless in both the admin table and the email.
type ListingFailure struct {
	PartID      string
	PartName    string
	PartType    string
	Reason      string
	Detail      *string
	Occurrences int
	FirstSeenAt time.Time
	LastSeenAt  time.Time
	NotifiedAt  *time.Time
	ResolvedAt  *time.Time
}

// RecordListingFailure upserts the failure for one part.
//
// Best-effort by contract: callers run it detached from the request that
// noticed the problem, because a read of the listings API must not start
// failing just because the bookkeeping about it did.
//
// A recurrence after the row was resolved reopens it AND clears notified_at, so
// the next digest reports it again — a part that breaks, gets fixed, and breaks
// again is news the second time too.
func (s *Store) RecordListingFailure(ctx context.Context, partID, reason, detail string) error {
	// A malformed part_id can't be a real coverage gap; it's a bad caller.
	// Checked here so it costs nothing at the database.
	if _, err := uuid.Parse(partID); err != nil {
		return nil
	}

	var detailArg any
	if detail != "" {
		detailArg = detail
	}

	_, err := s.db.ExecContext(ctx, `
		INSERT INTO listing_lookup_failures (part_id, reason, detail)
		VALUES ($1, $2, $3)
		ON CONFLICT (part_id) DO UPDATE SET
			reason      = EXCLUDED.reason,
			detail      = EXCLUDED.detail,
			occurrences = listing_lookup_failures.occurrences + 1,
			last_seen_at = now(),
			notified_at = CASE
				WHEN listing_lookup_failures.resolved_at IS NOT NULL THEN NULL
				ELSE listing_lookup_failures.notified_at
			END,
			resolved_at = NULL`,
		partID, reason, detailArg)

	// A part_id that is a valid UUID but not a real part violates the FK. That
	// is a caller passing a stale or invented id, not a coverage gap, and the
	// constraint is what tells us so — nothing to report.
	if isForeignKeyViolation(err) {
		return nil
	}
	return err
}

// ResolveListingFailure closes the open failure for a part, if there is one.
//
// Called when a listing is created or reactivated — the event that actually
// fixes a coverage gap. Deliberately not called on every successful lookup:
// that would put a write on the hottest read path in the service to update a
// row that almost never exists.
func (s *Store) ResolveListingFailure(ctx context.Context, partID string) error {
	if _, err := uuid.Parse(partID); err != nil {
		return nil
	}
	_, err := s.db.ExecContext(ctx,
		`UPDATE listing_lookup_failures SET resolved_at = now()
		 WHERE part_id = $1 AND resolved_at IS NULL`, partID)
	return err
}

const listingFailureColumns = `
	f.part_id, p.name, p.part_type, f.reason, f.detail, f.occurrences,
	f.first_seen_at, f.last_seen_at, f.notified_at, f.resolved_at`

func scanListingFailure(row interface{ Scan(dest ...any) error }) (*ListingFailure, error) {
	var f ListingFailure
	if err := row.Scan(
		&f.PartID, &f.PartName, &f.PartType, &f.Reason, &f.Detail, &f.Occurrences,
		&f.FirstSeenAt, &f.LastSeenAt, &f.NotifiedAt, &f.ResolvedAt,
	); err != nil {
		return nil, err
	}
	return &f, nil
}

// ListUnnotifiedListingFailures returns open failures that no digest has
// reported yet, worst first. limit caps one email's size: a bad deploy could
// open thousands of these at once, and a mail listing all of them would be
// unsendable as well as unreadable.
func (s *Store) ListUnnotifiedListingFailures(ctx context.Context, limit int) ([]*ListingFailure, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT `+listingFailureColumns+`
		FROM listing_lookup_failures f
		JOIN pc_parts p ON p.id = f.part_id
		WHERE f.resolved_at IS NULL AND f.notified_at IS NULL
		ORDER BY f.occurrences DESC, f.first_seen_at
		LIMIT $1`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []*ListingFailure
	for rows.Next() {
		f, err := scanListingFailure(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, f)
	}
	return out, rows.Err()
}

// MarkListingFailuresNotified stamps the rows a digest just reported.
//
// Runs after the send succeeds, not before: the cost of re-reporting a part in
// tomorrow's digest is a duplicate line, and the cost of stamping first and
// then failing to send is never hearing about it at all.
func (s *Store) MarkListingFailuresNotified(ctx context.Context, partIDs []string) error {
	if len(partIDs) == 0 {
		return nil
	}
	// Placeholders rather than `= ANY($1)` with a Go slice: array encoding
	// through database/sql depends on the driver, and this package is opened
	// two different ways (the Cloud SQL connector in prod, plain pgx in dev).
	// The list is bounded by the digest's own limit, so the SQL stays small.
	args := make([]any, len(partIDs))
	placeholders := make([]string, len(partIDs))
	for i, id := range partIDs {
		args[i] = id
		placeholders[i] = fmt.Sprintf("$%d", i+1)
	}
	_, err := s.db.ExecContext(ctx,
		`UPDATE listing_lookup_failures SET notified_at = now() WHERE part_id IN (`+
			strings.Join(placeholders, ", ")+`)`, args...)
	return err
}

// CountOpenListingFailures is the standing total, including rows already
// reported — what the digest quotes so a short "2 new" line still says whether
// the backlog is 2 or 200.
func (s *Store) CountOpenListingFailures(ctx context.Context) (int, error) {
	var n int
	err := s.db.QueryRowContext(ctx,
		`SELECT count(*) FROM listing_lookup_failures WHERE resolved_at IS NULL`).Scan(&n)
	return n, err
}
