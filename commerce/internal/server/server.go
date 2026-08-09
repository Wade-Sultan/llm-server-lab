// Package server wires routes and middleware into an http.Handler.
package server

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"time"

	firebase "firebase.google.com/go/v4"
	fbauth "firebase.google.com/go/v4/auth"

	"github.com/palladium/commerce/internal/config"
	"github.com/palladium/commerce/internal/email"
	"github.com/palladium/commerce/internal/store"
)

// dataStore is the slice of *store.Store the handlers actually call. It exists
// so tests can substitute a fake without a live Postgres; *store.Store is the
// only production implementation. Close() is deliberately absent — lifecycle
// belongs to New, which holds the concrete value.
type dataStore interface {
	Ping(ctx context.Context) error

	GetUserByFirebaseUID(ctx context.Context, firebaseUID string) (*store.User, error)
	GetUserByEmail(ctx context.Context, email string) (*store.User, error)
	CreateUser(ctx context.Context, firebaseUID, email string) (*store.User, error)
	LinkFirebaseUID(ctx context.Context, userID, firebaseUID string) (*store.User, error)
	DeleteUserByFirebaseUID(ctx context.Context, firebaseUID string) error

	ListListings(ctx context.Context, f store.ListingFilter) ([]*store.Listing, error)
	CountListings(ctx context.Context, f store.ListingFilter) (int, error)
	GetListingByID(ctx context.Context, id string) (*store.Listing, error)
	GetListingsByPartID(ctx context.Context, partID string) ([]*store.Listing, error)
	PartExists(ctx context.Context, partID string) (bool, error)
	CreateListing(ctx context.Context, in store.CreateListingInput) (*store.Listing, error)
	UpdateListing(ctx context.Context, id string, in store.UpdateListingInput) (*store.Listing, error)
	DeleteListing(ctx context.Context, id string) error
}

// mailer is the transactional-email seam, satisfied by *email.Client. Tests
// use it to assert which messages a handler triggers without hitting Resend.
type mailer interface {
	Enabled() bool
	Send(ctx context.Context, msg email.Message) error
}

// handlers holds the dependencies shared by all route handlers.
type handlers struct {
	store          dataStore
	auth           *fbauth.Client
	email          mailer
	associateTag   string
	ebayCampaignID string
	logger         *slog.Logger
}

// New builds the fully-configured HTTP handler for the commerce service. It
// establishes the DB pool and Firebase Admin client up front so the instance
// fails fast on misconfiguration rather than on the first request (same
// philosophy as config.Load()). The returned cleanup func must be called on
// shutdown.
func New(ctx context.Context, cfg *config.Config, logger *slog.Logger) (http.Handler, func() error, error) {
	st, err := store.New(ctx, cfg)
	if err != nil {
		return nil, nil, fmt.Errorf("init store: %w", err)
	}

	fbApp, err := firebase.NewApp(ctx, &firebase.Config{ProjectID: cfg.FirebaseProjectID})
	if err != nil {
		_ = st.Close()
		return nil, nil, fmt.Errorf("init firebase app: %w", err)
	}
	fbAuth, err := fbApp.Auth(ctx)
	if err != nil {
		_ = st.Close()
		return nil, nil, fmt.Errorf("init firebase auth client: %w", err)
	}

	h := &handlers{
		store:          st,
		auth:           fbAuth,
		email:          email.New(cfg.ResendAPIKey, cfg.EmailFrom),
		associateTag:   cfg.AssociateTag,
		ebayCampaignID: cfg.EbayCampaignID,
		logger:         logger,
	}

	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", handleHealthz)
	mux.HandleFunc("GET /readyz", h.handleReadyz)

	// Listings — reads are public today (no auth dependency in the Python
	// route either), writes require a valid Firebase user. Kept path-identical
	// to backend/app/api/routes/listings.py so the frontend only swaps a base
	// URL, not the paths themselves.
	mux.HandleFunc("GET /api/v1/listings/", h.listListings)
	mux.HandleFunc("GET /api/v1/listings/{id}", h.getListing)
	mux.HandleFunc("GET /api/v1/listings/by-part/{part_id}", h.getListingsByPart)
	mux.Handle("POST /api/v1/listings/", requireFirebaseAuth(fbAuth)(http.HandlerFunc(h.createListing)))
	mux.Handle("PATCH /api/v1/listings/{id}", requireFirebaseAuth(fbAuth)(http.HandlerFunc(h.updateListing)))
	mux.Handle("DELETE /api/v1/listings/{id}", requireFirebaseAuth(fbAuth)(http.HandlerFunc(h.deleteListing)))

	// Account — new surface replacing the retired bcrypt/JWT users.py routes.
	// Paths deliberately differ from the old /users/* surface since this is a
	// Firebase-account-sync model, not generic user CRUD.
	mux.Handle("POST /api/v1/account/sync", requireFirebaseAuth(fbAuth)(http.HandlerFunc(h.syncAccount)))
	mux.Handle("DELETE /api/v1/account", requireFirebaseAuth(fbAuth)(http.HandlerFunc(h.deleteAccount)))

	// Middleware wraps outermost-first: recoverPanic is the outer shell so it
	// can catch panics from everything inside, including the logger.
	var handler http.Handler = mux
	handler = cors(cfg.CORSOrigins)(handler)
	handler = requestLogger(logger)(handler)
	handler = recoverPanic(logger)(handler)
	// Outside recoverPanic, not inside: a handler that panics must be counted
	// as the 5xx the client actually received. Inside, the deferred record
	// would still run during unwinding but would see the untouched 200 default,
	// so panics would silently show up in the dashboards as successes.
	handler = requestMetrics(mux)(handler)

	cleanup := func() error { return st.Close() }
	return handler, cleanup, nil
}

// handleHealthz is a liveness probe. Cloud Run and uptime checks hit this.
// It deliberately touches nothing external — a database hiccup must not get
// an otherwise-healthy instance restarted.
func handleHealthz(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

// handleReadyz is the readiness probe: can this instance actually serve? The
// short timeout matters — the probe's own period is the budget, so a hung
// pool should fail fast rather than pile probe goroutines up behind it.
func (h *handlers) handleReadyz(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
	defer cancel()

	if err := h.store.Ping(ctx); err != nil {
		h.logger.Warn("readiness check failed", "err", err)
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{
			"status": "not_ready",
			"reason": "database",
		})
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "ready"})
}

// writeJSON is the shared response helper for JSON endpoints.
func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(v); err != nil {
		slog.Error("failed to encode response", "err", err)
	}
}
