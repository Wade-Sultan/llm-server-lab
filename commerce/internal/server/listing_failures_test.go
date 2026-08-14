package server

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/palladium/commerce/internal/store"
)

// failureStore is the listing-failure half of dataStore. Recording happens in a
// goroutine detached from the request (see noteListingFailure), so recorded
// calls arrive on a channel rather than a slice a test could read too early.
type failureStore struct {
	dataStore

	listings   []*store.Listing // returned by both listing lookups
	listErr    error
	partExists bool

	recorded chan [3]string // part_id, reason, detail
	resolved chan string    // part_id

	open      []*store.ListingFailure
	openCount int
	listFail  error
	notified  chan []string
}

func newFailureStore() *failureStore {
	return &failureStore{
		partExists: true,
		recorded:   make(chan [3]string, 8),
		resolved:   make(chan string, 8),
		notified:   make(chan []string, 4),
	}
}

func (f *failureStore) ListListings(context.Context, store.ListingFilter) ([]*store.Listing, error) {
	return f.listings, f.listErr
}
func (f *failureStore) CountListings(context.Context, store.ListingFilter) (int, error) {
	return len(f.listings), nil
}
func (f *failureStore) PartExists(context.Context, string) (bool, error) {
	return f.partExists, nil
}
func (f *failureStore) GetListingsByPartID(context.Context, string) ([]*store.Listing, error) {
	return f.listings, f.listErr
}

func (f *failureStore) RecordListingFailure(_ context.Context, partID, reason, detail string) error {
	f.recorded <- [3]string{partID, reason, detail}
	return nil
}

func (f *failureStore) ResolveListingFailure(_ context.Context, partID string) error {
	f.resolved <- partID
	return nil
}

func (f *failureStore) ListUnnotifiedListingFailures(_ context.Context, limit int) ([]*store.ListingFailure, error) {
	if f.listFail != nil {
		return nil, f.listFail
	}
	if len(f.open) > limit {
		return f.open[:limit], nil
	}
	return f.open, nil
}

func (f *failureStore) CountOpenListingFailures(context.Context) (int, error) {
	return f.openCount, nil
}

func (f *failureStore) MarkListingFailuresNotified(_ context.Context, partIDs []string) error {
	f.notified <- partIDs
	return nil
}

func (f *failureStore) CreateListing(_ context.Context, in store.CreateListingInput) (*store.Listing, error) {
	return listing(in.PartID), nil
}

func (f *failureStore) expectResolved(t *testing.T) string {
	t.Helper()
	select {
	case got := <-f.resolved:
		return got
	case <-time.After(2 * time.Second):
		t.Fatal("expected the failure to be resolved, it was not")
		return ""
	}
}

func (f *failureStore) expectRecorded(t *testing.T) [3]string {
	t.Helper()
	select {
	case got := <-f.recorded:
		return got
	case <-time.After(2 * time.Second):
		t.Fatal("expected a recorded listing failure, got none")
		return [3]string{}
	}
}

// expectNoRecord asserts nothing was recorded. The wait is a real (short)
// sleep: an absent write can only be observed by its continued absence.
func (f *failureStore) expectNoRecord(t *testing.T) {
	t.Helper()
	select {
	case got := <-f.recorded:
		t.Fatalf("expected no failure recorded, got %v", got)
	case <-time.After(150 * time.Millisecond):
	}
}

func newFailureHandlers(st dataStore, m mailer, opsEmail string) *handlers {
	h := newTestHandlers(st, m)
	h.opsEmail = opsEmail
	return h
}

func listing(partID string) *store.Listing {
	return &store.Listing{ID: "l1", PartID: partID, ListingType: "amazon", Marketplace: "amazon", IsActive: true}
}

// ---------------------------------------------------------------------------
// Recording
// ---------------------------------------------------------------------------

const partID = "6f1b2c3d-4e5f-4a6b-8c9d-0e1f2a3b4c5d"

func TestListingLookupRecording(t *testing.T) {
	t.Run("empty result for a part is a coverage gap", func(t *testing.T) {
		st := newFailureStore()
		rec := httptest.NewRecorder()
		r := httptest.NewRequest("GET", "/api/v1/listings/?part_id="+partID, nil)

		newFailureHandlers(st, newFakeMailer(), "ops@example.com").listListings(rec, r)

		if rec.Code != http.StatusOK {
			t.Fatalf("status = %d, want 200", rec.Code)
		}
		got := st.expectRecorded(t)
		if got[0] != partID || got[1] != store.ReasonNoActiveListing {
			t.Errorf("recorded %v, want (%s, %s)", got, partID, store.ReasonNoActiveListing)
		}
	})

	t.Run("a lookup error is recorded as an error, not a gap", func(t *testing.T) {
		st := newFailureStore()
		st.listErr = errors.New("connection refused")
		rec := httptest.NewRecorder()
		r := httptest.NewRequest("GET", "/api/v1/listings/?part_id="+partID, nil)

		newFailureHandlers(st, newFakeMailer(), "ops@example.com").listListings(rec, r)

		if rec.Code != http.StatusInternalServerError {
			t.Fatalf("status = %d, want 500", rec.Code)
		}
		got := st.expectRecorded(t)
		if got[1] != store.ReasonLookupError {
			t.Errorf("reason = %q, want %q", got[1], store.ReasonLookupError)
		}
		if !strings.Contains(got[2], "connection refused") {
			t.Errorf("detail = %q, want it to carry the underlying error", got[2])
		}
	})

	t.Run("a result is not a failure", func(t *testing.T) {
		st := newFailureStore()
		st.listings = []*store.Listing{listing(partID)}
		rec := httptest.NewRecorder()
		r := httptest.NewRequest("GET", "/api/v1/listings/?part_id="+partID, nil)

		newFailureHandlers(st, newFakeMailer(), "ops@example.com").listListings(rec, r)

		st.expectNoRecord(t)
	})

	t.Run("an unfiltered listing sweep blames no part", func(t *testing.T) {
		// Without a part_id there is nothing to record against — an empty
		// catalog-wide result is not one part's problem.
		st := newFailureStore()
		rec := httptest.NewRecorder()
		r := httptest.NewRequest("GET", "/api/v1/listings/", nil)

		newFailureHandlers(st, newFakeMailer(), "ops@example.com").listListings(rec, r)

		st.expectNoRecord(t)
	})

	t.Run("by-part lookup records an empty result too", func(t *testing.T) {
		st := newFailureStore()
		rec := httptest.NewRecorder()
		r := httptest.NewRequest("GET", "/api/v1/listings/by-part/"+partID, nil)
		r.SetPathValue("part_id", partID)

		newFailureHandlers(st, newFakeMailer(), "ops@example.com").getListingsByPart(rec, r)

		if rec.Code != http.StatusOK {
			t.Fatalf("status = %d, want 200", rec.Code)
		}
		if got := st.expectRecorded(t); got[1] != store.ReasonNoActiveListing {
			t.Errorf("reason = %q", got[1])
		}
	})

	t.Run("an unknown part is a bad request, not a coverage gap", func(t *testing.T) {
		st := newFailureStore()
		st.partExists = false
		rec := httptest.NewRecorder()
		r := httptest.NewRequest("GET", "/api/v1/listings/by-part/"+partID, nil)
		r.SetPathValue("part_id", partID)

		newFailureHandlers(st, newFakeMailer(), "ops@example.com").getListingsByPart(rec, r)

		if rec.Code != http.StatusNotFound {
			t.Fatalf("status = %d, want 404", rec.Code)
		}
		st.expectNoRecord(t)
	})
}

// Adding a listing is the event that actually closes a coverage gap, and is
// why resolution hangs off writes rather than off every successful read.
func TestCreatingAListingResolvesTheFailure(t *testing.T) {
	st := newFailureStore()
	rec := httptest.NewRecorder()
	body := `{"part_id":"` + partID + `","marketplace":"amazon","asin":"B01ABCDEFG"}`
	r := httptest.NewRequest("POST", "/api/v1/listings/", strings.NewReader(body))

	newFailureHandlers(st, newFakeMailer(), "ops@example.com").createListing(rec, r)

	if rec.Code != http.StatusCreated {
		t.Fatalf("status = %d, want 201: %s", rec.Code, rec.Body.String())
	}
	if got := st.expectResolved(t); got != partID {
		t.Errorf("resolved %q, want %q", got, partID)
	}
}

// ---------------------------------------------------------------------------
// The digest
// ---------------------------------------------------------------------------

func openFailure(i int) *store.ListingFailure {
	return &store.ListingFailure{
		PartID:      fmt.Sprintf("part-%d", i),
		PartName:    fmt.Sprintf("Test Part %d", i),
		PartType:    "cpu",
		Reason:      store.ReasonNoActiveListing,
		Occurrences: i + 1,
	}
}

func digestRequest() *http.Request {
	return httptest.NewRequest("POST", "/internal/v1/listing-failure-digest", nil)
}

func TestListingFailureDigest(t *testing.T) {
	st, m := newFailureStore(), &syncMailer{enabled: true}
	st.open = []*store.ListingFailure{openFailure(0), openFailure(1)}
	st.openCount = 7
	rec := httptest.NewRecorder()

	newFailureHandlers(st, m, "ops@example.com").sendListingFailureDigest(rec, digestRequest())

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200: %s", rec.Code, rec.Body.String())
	}
	if len(m.sent) != 1 {
		t.Fatalf("sent %d emails, want 1", len(m.sent))
	}
	msg := m.sent[0]
	if msg.To != "ops@example.com" {
		t.Errorf("To = %q, want the configured ops address", msg.To)
	}
	if want := "Palladium: 2 parts with no listing"; msg.Subject != want {
		t.Errorf("Subject = %q, want %q", msg.Subject, want)
	}
	for _, part := range []string{"Test Part 0", "Test Part 1", "no active listing"} {
		if !strings.Contains(msg.HTML, part) {
			t.Errorf("HTML is missing %q", part)
		}
	}
	// The standing backlog is quoted alongside what is new, so a two-line
	// digest still says whether things are getting worse.
	if !strings.Contains(msg.Text, "7 open in total") {
		t.Errorf("text body does not mention the open total: %q", msg.Text)
	}

	select {
	case ids := <-st.notified:
		if len(ids) != 2 {
			t.Errorf("marked %d rows notified, want 2", len(ids))
		}
	case <-time.After(2 * time.Second):
		t.Fatal("the reported rows were never marked notified")
	}
}

// Nothing new is the healthy steady state. It must be a success with no email,
// not a failure the CronJob goes red over.
func TestListingFailureDigestWithNothingToReport(t *testing.T) {
	st, m := newFailureStore(), &syncMailer{enabled: true}
	rec := httptest.NewRecorder()

	newFailureHandlers(st, m, "ops@example.com").sendListingFailureDigest(rec, digestRequest())

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rec.Code)
	}
	if len(m.sent) != 0 {
		t.Errorf("sent an email with nothing to report")
	}
	if !strings.Contains(rec.Body.String(), "nothing_to_report") {
		t.Errorf("body = %q, want it to say nothing was reported", rec.Body.String())
	}
}

func TestListingFailureDigestTruncatesAHugeBacklog(t *testing.T) {
	st, m := newFailureStore(), &syncMailer{enabled: true}
	for i := 0; i < maxDigestRows+10; i++ {
		st.open = append(st.open, openFailure(i))
	}
	st.openCount = len(st.open)
	rec := httptest.NewRecorder()

	newFailureHandlers(st, m, "ops@example.com").sendListingFailureDigest(rec, digestRequest())

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200: %s", rec.Code, rec.Body.String())
	}
	if !strings.Contains(m.sent[0].HTML, "Only the worst") {
		t.Error("a truncated digest does not say it was truncated")
	}
	// Everything fetched is marked notified, including the overflow: repeating
	// the same wall of text daily adds no signal.
	ids := <-st.notified
	if len(ids) != maxDigestRows {
		t.Errorf("marked %d rows notified, want %d", len(ids), maxDigestRows)
	}
}

// Nothing may be marked notified unless the mail actually went out — a row
// stamped after a failed send would never appear in another digest.
func TestListingFailureDigestDoesNotMarkNotifiedOnFailure(t *testing.T) {
	for _, tt := range []struct {
		name     string
		opsEmail string
		mutate   func(*syncMailer)
		wantCode int
	}{
		{"no ops address configured", "", nil, http.StatusServiceUnavailable},
		{"email not configured", "ops@example.com", func(m *syncMailer) { m.enabled = false }, http.StatusServiceUnavailable},
		{"send fails", "ops@example.com", func(m *syncMailer) { m.sendErr = http.ErrHandlerTimeout }, http.StatusBadGateway},
	} {
		t.Run(tt.name, func(t *testing.T) {
			st, m := newFailureStore(), &syncMailer{enabled: true}
			st.open = []*store.ListingFailure{openFailure(0)}
			if tt.mutate != nil {
				tt.mutate(m)
			}
			rec := httptest.NewRecorder()

			newFailureHandlers(st, m, tt.opsEmail).sendListingFailureDigest(rec, digestRequest())

			if rec.Code != tt.wantCode {
				t.Errorf("status = %d, want %d", rec.Code, tt.wantCode)
			}
			select {
			case ids := <-st.notified:
				t.Errorf("marked %v notified despite not sending", ids)
			case <-time.After(150 * time.Millisecond):
			}
		})
	}
}
