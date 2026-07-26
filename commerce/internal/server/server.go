// Package server wires routes and middleware into an http.Handler.
package server

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"

	firebase "firebase.google.com/go/v4"
	fbauth "firebase.google.com/go/v4/auth"

	"github.com/palladium/commerce/internal/config"
	"github.com/palladium/commerce/internal/email"
	"github.com/palladium/commerce/internal/store"
)

// handlers holds the dependencies shared by all route handlers.
type handlers struct {
	store          *store.Store
	auth           *fbauth.Client
	email          *email.Client
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

	cleanup := func() error { return st.Close() }
	return handler, cleanup, nil
}

// handleHealthz is a liveness probe. Cloud Run and uptime checks hit this.
func handleHealthz(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

// writeJSON is the shared response helper for JSON endpoints.
func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(v); err != nil {
		slog.Error("failed to encode response", "err", err)
	}
}
