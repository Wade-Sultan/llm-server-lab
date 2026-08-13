package server

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/palladium/commerce/internal/email"
	"github.com/palladium/commerce/internal/store"
)

// GetUserByID completes the user half of dataStore for the internal handler.
// fakeStore itself lives in account_handlers_test.go.
func (f *fakeStore) GetUserByID(_ context.Context, id string) (*store.User, error) {
	for _, u := range f.byEmail {
		if u.ID == id {
			return u, nil
		}
	}
	return nil, store.ErrNotFound
}

// syncMailer sends on the calling goroutine, unlike fakeMailer's channel. The
// price-alert handler is deliberately synchronous — its response is what tells
// the builder whether to retire a subscription — so the test asserts on what
// was sent by the time ServeHTTP returned.
type syncMailer struct {
	enabled bool
	sendErr error
	sent    []email.Message
}

func (m *syncMailer) Enabled() bool { return m.enabled }

func (m *syncMailer) Send(_ context.Context, msg email.Message) error {
	if m.sendErr != nil {
		return m.sendErr
	}
	m.sent = append(m.sent, msg)
	return nil
}

func alertBody(t *testing.T, body map[string]any) *http.Request {
	t.Helper()
	raw, err := json.Marshal(body)
	if err != nil {
		t.Fatalf("marshal body: %v", err)
	}
	return httptest.NewRequest("POST", "/internal/v1/price-alerts", strings.NewReader(string(raw)))
}

func validAlert(userID string) map[string]any {
	return map[string]any{
		"user_id":   userID,
		"part_name": "AMD Ryzen 7 9800X3D",
		"old_cents": 54999,
		"new_cents": 42950,
		"currency":  "USD",
	}
}

func TestSendPriceAlert(t *testing.T) {
	st, m := newFakeStore(), &syncMailer{enabled: true}
	st.seed("u1", "uid-1", "wade@example.com")
	rec := httptest.NewRecorder()

	newTestHandlers(st, m).sendPriceAlert(rec, alertBody(t, validAlert("u1")))

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200: %s", rec.Code, rec.Body.String())
	}
	if len(m.sent) != 1 {
		t.Fatalf("sent %d emails, want 1", len(m.sent))
	}
	// The address comes from the users row, never from the request — that is
	// the whole reason the builder passes an id.
	if m.sent[0].To != "wade@example.com" {
		t.Errorf("To = %q, want the address on the user row", m.sent[0].To)
	}
	if !strings.Contains(m.sent[0].Subject, "$429.50") {
		t.Errorf("Subject = %q, want the new price", m.sent[0].Subject)
	}
}

// Every rejection path must leave nothing sent: the builder retires a
// subscription on a 2xx, so a wrong success is a silently-lost alert.
func TestSendPriceAlertRejections(t *testing.T) {
	for _, tt := range []struct {
		name     string
		body     map[string]any
		mutate   func(*fakeStore, *syncMailer)
		wantCode int
	}{
		{
			name:     "unknown user",
			body:     validAlert("nobody"),
			wantCode: http.StatusNotFound,
		},
		{
			name:     "missing user_id",
			body:     map[string]any{"part_name": "X", "old_cents": 100, "new_cents": 50},
			wantCode: http.StatusUnprocessableEntity,
		},
		{
			name:     "missing part_name",
			body:     map[string]any{"user_id": "u1", "old_cents": 100, "new_cents": 50},
			wantCode: http.StatusUnprocessableEntity,
		},
		{
			name: "not a drop",
			body: func() map[string]any {
				b := validAlert("u1")
				b["new_cents"], b["old_cents"] = 54999, 42950
				return b
			}(),
			wantCode: http.StatusUnprocessableEntity,
		},
		{
			// A no-op mailer returns nil from Send, so answering 200 here would
			// retire a subscription whose email never existed.
			name:     "email not configured",
			body:     validAlert("u1"),
			mutate:   func(_ *fakeStore, m *syncMailer) { m.enabled = false },
			wantCode: http.StatusServiceUnavailable,
		},
		{
			name:     "send fails",
			body:     validAlert("u1"),
			mutate:   func(_ *fakeStore, m *syncMailer) { m.sendErr = http.ErrHandlerTimeout },
			wantCode: http.StatusBadGateway,
		},
		{
			name:     "inactive account",
			body:     validAlert("u1"),
			mutate:   func(st *fakeStore, _ *syncMailer) { st.byEmail["wade@example.com"].IsActive = false },
			wantCode: http.StatusConflict,
		},
	} {
		t.Run(tt.name, func(t *testing.T) {
			st, m := newFakeStore(), &syncMailer{enabled: true}
			st.seed("u1", "uid-1", "wade@example.com")
			if tt.mutate != nil {
				tt.mutate(st, m)
			}
			rec := httptest.NewRecorder()

			newTestHandlers(st, m).sendPriceAlert(rec, alertBody(t, tt.body))

			if rec.Code != tt.wantCode {
				t.Errorf("status = %d, want %d: %s", rec.Code, tt.wantCode, rec.Body.String())
			}
			if len(m.sent) != 0 {
				t.Errorf("sent %d emails on a rejected request, want 0", len(m.sent))
			}
		})
	}
}

func TestRequireInternalKey(t *testing.T) {
	reached := false
	next := http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		reached = true
		w.WriteHeader(http.StatusOK)
	})

	for _, tt := range []struct {
		name                  string
		configured, presented string
		wantCode              int
		wantThrough           bool
	}{
		{"correct key passes", "s3cret", "s3cret", http.StatusOK, true},
		{"wrong key is forbidden", "s3cret", "guess", http.StatusForbidden, false},
		{"missing key is forbidden", "s3cret", "", http.StatusForbidden, false},
		// Unconfigured must close the route, not open it.
		{"unconfigured is unavailable", "", "anything", http.StatusServiceUnavailable, false},
		{"unconfigured rejects an empty header too", "", "", http.StatusServiceUnavailable, false},
	} {
		t.Run(tt.name, func(t *testing.T) {
			reached = false
			r := httptest.NewRequest("POST", "/internal/v1/price-alerts", nil)
			if tt.presented != "" {
				r.Header.Set("X-Internal-Key", tt.presented)
			}
			rec := httptest.NewRecorder()

			requireInternalKey(tt.configured)(next).ServeHTTP(rec, r)

			if rec.Code != tt.wantCode {
				t.Errorf("status = %d, want %d", rec.Code, tt.wantCode)
			}
			if reached != tt.wantThrough {
				t.Errorf("handler reached = %v, want %v", reached, tt.wantThrough)
			}
		})
	}
}
