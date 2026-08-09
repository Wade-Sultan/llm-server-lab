package server

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	fbauth "firebase.google.com/go/v4/auth"

	"github.com/palladium/commerce/internal/email"
	"github.com/palladium/commerce/internal/store"
)

// fakeStore implements only the user half of dataStore; the embedded interface
// supplies the listing methods as nil, so a handler that unexpectedly reaches
// for one panics loudly instead of silently passing.
type fakeStore struct {
	dataStore

	byUID   map[string]*store.User
	byEmail map[string]*store.User

	created []string // emails passed to CreateUser, in call order
	linked  []string // user IDs passed to LinkFirebaseUID, in call order

	createErr error
	deleteErr error
}

func newFakeStore() *fakeStore {
	return &fakeStore{byUID: map[string]*store.User{}, byEmail: map[string]*store.User{}}
}

// seed registers an existing row. Pass an empty uid for a row that has no
// firebase_uid yet — the auto-provisioned-by-builder case.
func (f *fakeStore) seed(id, uid, addr string) *store.User {
	u := &store.User{ID: id, Email: addr, IsActive: true}
	if uid != "" {
		u.FirebaseUID = &uid
		f.byUID[uid] = u
	}
	f.byEmail[addr] = u
	return u
}

func (f *fakeStore) GetUserByFirebaseUID(_ context.Context, uid string) (*store.User, error) {
	if u, ok := f.byUID[uid]; ok {
		return u, nil
	}
	return nil, store.ErrNotFound
}

func (f *fakeStore) GetUserByEmail(_ context.Context, addr string) (*store.User, error) {
	if u, ok := f.byEmail[addr]; ok {
		return u, nil
	}
	return nil, store.ErrNotFound
}

func (f *fakeStore) CreateUser(_ context.Context, uid, addr string) (*store.User, error) {
	f.created = append(f.created, addr)
	if f.createErr != nil {
		return nil, f.createErr
	}
	u := &store.User{ID: "new-" + uid, FirebaseUID: &uid, Email: addr, IsActive: true}
	f.byUID[uid] = u
	f.byEmail[addr] = u
	return u, nil
}

func (f *fakeStore) LinkFirebaseUID(_ context.Context, userID, uid string) (*store.User, error) {
	f.linked = append(f.linked, userID)
	for _, u := range f.byEmail {
		if u.ID == userID {
			u.FirebaseUID = &uid
			f.byUID[uid] = u
			return u, nil
		}
	}
	return nil, store.ErrNotFound
}

func (f *fakeStore) DeleteUserByFirebaseUID(_ context.Context, uid string) error {
	if f.deleteErr != nil {
		return f.deleteErr
	}
	if _, ok := f.byUID[uid]; !ok {
		return store.ErrNotFound
	}
	delete(f.byUID, uid)
	return nil
}

// fakeMailer records sends on a channel. sendEmailAsync dispatches in a
// goroutine detached from the request, so tests must wait on the channel
// rather than inspecting a slice right after ServeHTTP returns.
type fakeMailer struct {
	enabled bool
	sent    chan email.Message
}

func newFakeMailer() *fakeMailer {
	return &fakeMailer{enabled: true, sent: make(chan email.Message, 4)}
}

func (m *fakeMailer) Enabled() bool { return m.enabled }

func (m *fakeMailer) Send(_ context.Context, msg email.Message) error {
	m.sent <- msg
	return nil
}

// expect waits for one send and returns it.
func (m *fakeMailer) expect(t *testing.T) email.Message {
	t.Helper()
	select {
	case msg := <-m.sent:
		return msg
	case <-time.After(2 * time.Second):
		t.Fatal("expected an email, none was sent")
		return email.Message{}
	}
}

// expectNone asserts nothing is sent. The wait is a real (short) sleep: a
// missing send can only be observed by its continued absence.
func (m *fakeMailer) expectNone(t *testing.T) {
	t.Helper()
	select {
	case msg := <-m.sent:
		t.Fatalf("expected no email, got %q to %s", msg.Subject, msg.To)
	case <-time.After(150 * time.Millisecond):
	}
}

func newTestHandlers(st dataStore, m mailer) *handlers {
	return &handlers{
		store:  st,
		email:  m,
		logger: slog.New(slog.NewTextHandler(io.Discard, nil)),
	}
}

// request builds a request carrying a decoded Firebase token, standing in for
// requireFirebaseAuth (which needs a live Firebase Admin client). Pass a nil
// claims map to simulate a token with no email claim.
func request(method, target, uid string, claims map[string]any) *http.Request {
	r := httptest.NewRequest(method, target, nil)
	if uid == "" {
		return r
	}
	token := &fbauth.Token{UID: uid, Claims: claims}
	return r.WithContext(context.WithValue(r.Context(), firebaseTokenContextKey, token))
}

func claims(addr string) map[string]any { return map[string]any{"email": addr} }

// The welcome email is the whole point of the created flag: it must fire for a
// genuinely new row and for nothing else. Each case below is a path that
// reaches syncAccount in production.
func TestSyncAccountWelcomeEmail(t *testing.T) {
	t.Run("new user gets a welcome email", func(t *testing.T) {
		st, m := newFakeStore(), newFakeMailer()
		rec := httptest.NewRecorder()

		newTestHandlers(st, m).syncAccount(rec, request("POST", "/api/v1/account/sync", "uid-1", claims("new@example.com")))

		if rec.Code != http.StatusOK {
			t.Fatalf("status = %d, want 200", rec.Code)
		}
		msg := m.expect(t)
		if msg.To != "new@example.com" {
			t.Errorf("To = %q, want new@example.com", msg.To)
		}
		if msg.Subject != "Welcome to Palladium" {
			t.Errorf("Subject = %q, want the welcome subject", msg.Subject)
		}
		if len(st.created) != 1 {
			t.Errorf("CreateUser called %d times, want 1", len(st.created))
		}
	})

	t.Run("returning user gets no email", func(t *testing.T) {
		st, m := newFakeStore(), newFakeMailer()
		st.seed("u1", "uid-1", "existing@example.com")
		rec := httptest.NewRecorder()

		newTestHandlers(st, m).syncAccount(rec, request("POST", "/api/v1/account/sync", "uid-1", claims("existing@example.com")))

		if rec.Code != http.StatusOK {
			t.Fatalf("status = %d, want 200", rec.Code)
		}
		m.expectNone(t)
		if len(st.created) != 0 {
			t.Errorf("CreateUser called on a re-sync")
		}
	})

	// The row already existed (builder's chat pipeline auto-provisions by
	// email) and this sync only attaches the firebase_uid. Not a signup, so no
	// welcome — this is the branch the created flag exists to exclude.
	t.Run("linking an auto-provisioned row sends no email", func(t *testing.T) {
		st, m := newFakeStore(), newFakeMailer()
		st.seed("u2", "", "prior@example.com")
		rec := httptest.NewRecorder()

		newTestHandlers(st, m).syncAccount(rec, request("POST", "/api/v1/account/sync", "uid-2", claims("prior@example.com")))

		if rec.Code != http.StatusOK {
			t.Fatalf("status = %d, want 200", rec.Code)
		}
		m.expectNone(t)
		if len(st.linked) != 1 {
			t.Errorf("LinkFirebaseUID called %d times, want 1", len(st.linked))
		}
	})

	// A failed insert must not produce a welcome email for an account that
	// doesn't exist.
	t.Run("failed create sends no email", func(t *testing.T) {
		st, m := newFakeStore(), newFakeMailer()
		st.createErr = context.DeadlineExceeded
		rec := httptest.NewRecorder()

		newTestHandlers(st, m).syncAccount(rec, request("POST", "/api/v1/account/sync", "uid-3", claims("boom@example.com")))

		if rec.Code != http.StatusInternalServerError {
			t.Fatalf("status = %d, want 500", rec.Code)
		}
		m.expectNone(t)
	})

	// Resend unconfigured (no API key) is a supported prod state: the handler
	// must still succeed.
	t.Run("succeeds with email disabled", func(t *testing.T) {
		st, m := newFakeStore(), newFakeMailer()
		m.enabled = false
		rec := httptest.NewRecorder()

		newTestHandlers(st, m).syncAccount(rec, request("POST", "/api/v1/account/sync", "uid-4", claims("quiet@example.com")))

		if rec.Code != http.StatusOK {
			t.Fatalf("status = %d, want 200", rec.Code)
		}
		m.expectNone(t)
	})
}

func TestSyncAccountResponse(t *testing.T) {
	st, m := newFakeStore(), newFakeMailer()
	st.seed("u1", "uid-1", "existing@example.com")
	rec := httptest.NewRecorder()

	newTestHandlers(st, m).syncAccount(rec, request("POST", "/api/v1/account/sync", "uid-1", claims("existing@example.com")))

	var got userDTO
	if err := json.NewDecoder(rec.Body).Decode(&got); err != nil {
		t.Fatalf("decode body: %v", err)
	}
	if got.ID != "u1" || got.Email != "existing@example.com" {
		t.Errorf("body = %+v, want the seeded user", got)
	}
	if got.FirebaseUID == nil || *got.FirebaseUID != "uid-1" {
		t.Errorf("firebase_uid not surfaced in the response")
	}
}

func TestSyncAccountRejects(t *testing.T) {
	tests := []struct {
		name   string
		uid    string
		claims map[string]any
		want   int
	}{
		{"no token in context", "", nil, http.StatusUnauthorized},
		{"unknown uid and no email claim", "uid-x", map[string]any{}, http.StatusUnprocessableEntity},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			st, m := newFakeStore(), newFakeMailer()
			rec := httptest.NewRecorder()

			newTestHandlers(st, m).syncAccount(rec, request("POST", "/api/v1/account/sync", tt.uid, tt.claims))

			if rec.Code != tt.want {
				t.Fatalf("status = %d, want %d", rec.Code, tt.want)
			}
			m.expectNone(t)
		})
	}
}

// deleteAccount's confirmation email is the mirror case: it fires only on a
// delete that actually happened.
func TestDeleteAccountEmail(t *testing.T) {
	t.Run("successful delete confirms by email", func(t *testing.T) {
		st, m := newFakeStore(), newFakeMailer()
		st.seed("u1", "uid-1", "bye@example.com")
		rec := httptest.NewRecorder()

		newTestHandlers(st, m).deleteAccount(rec, request("DELETE", "/api/v1/account", "uid-1", claims("bye@example.com")))

		if rec.Code != http.StatusOK {
			t.Fatalf("status = %d, want 200", rec.Code)
		}
		if msg := m.expect(t); msg.To != "bye@example.com" {
			t.Errorf("To = %q, want bye@example.com", msg.To)
		}
	})

	t.Run("conflict sends no email", func(t *testing.T) {
		st, m := newFakeStore(), newFakeMailer()
		st.seed("u1", "uid-1", "bye@example.com")
		st.deleteErr = store.ErrConflict
		rec := httptest.NewRecorder()

		newTestHandlers(st, m).deleteAccount(rec, request("DELETE", "/api/v1/account", "uid-1", claims("bye@example.com")))

		if rec.Code != http.StatusConflict {
			t.Fatalf("status = %d, want 409", rec.Code)
		}
		m.expectNone(t)
	})
}
