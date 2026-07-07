package server

import (
	"context"
	"log/slog"
	"net/http"
	"runtime/debug"
	"strings"
	"time"

	fbauth "firebase.google.com/go/v4/auth"
)

type middleware func(http.Handler) http.Handler

type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (r *statusRecorder) WriteHeader(code int) {
	r.status = code
	r.ResponseWriter.WriteHeader(code)
}

func requestLogger(logger *slog.Logger) middleware {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			start := time.Now()
			rec := &statusRecorder{ResponseWriter: w, status: http.StatusOK}

			next.ServeHTTP(rec, r)

			logger.Info("request",
				"method", r.Method,
				"path", r.URL.Path,
				"status", rec.status,
				"duration_ms", time.Since(start).Milliseconds(),
			)
		})
	}
}

// recoverPanic converts a panic into a 500 response instead of crashing the
// whole instance, and logs the stack for debugging.
func recoverPanic(logger *slog.Logger) middleware {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			defer func() {
				if err := recover(); err != nil {
					logger.Error("panic recovered",
						"err", err,
						"path", r.URL.Path,
						"stack", string(debug.Stack()),
					)
					http.Error(w, `{"error":"internal server error"}`, http.StatusInternalServerError)
				}
			}()

			next.ServeHTTP(w, r)
		})
	}
}

// ---------------------------------------------------------------------------
// Firebase auth
// ---------------------------------------------------------------------------

type contextKey string

const firebaseTokenContextKey contextKey = "firebaseToken"

// FirebaseTokenFromContext returns the decoded Firebase ID token attached by
// requireFirebaseAuth/optionalFirebaseAuth, if any.
func FirebaseTokenFromContext(ctx context.Context) (*fbauth.Token, bool) {
	t, ok := ctx.Value(firebaseTokenContextKey).(*fbauth.Token)
	return t, ok
}

func bearerToken(r *http.Request) (string, bool) {
	h := r.Header.Get("Authorization")
	const prefix = "Bearer "
	if !strings.HasPrefix(h, prefix) {
		return "", false
	}
	return strings.TrimPrefix(h, prefix), true
}

// requireFirebaseAuth verifies the Authorization: Bearer <token> header via
// the Firebase Admin SDK. Missing/invalid/expired tokens get a 401, mirroring
// backend/app/core/auth.py's verify_firebase_token.
func requireFirebaseAuth(client *fbauth.Client) middleware {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			raw, ok := bearerToken(r)
			if !ok {
				writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "missing authentication token"})
				return
			}
			token, err := client.VerifyIDToken(r.Context(), raw)
			if err != nil {
				writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "invalid or expired authentication token"})
				return
			}
			ctx := context.WithValue(r.Context(), firebaseTokenContextKey, token)
			next.ServeHTTP(w, r.WithContext(ctx))
		})
	}
}

// optionalFirebaseAuth verifies the token if present, but treats a
// missing/invalid token as a guest rather than rejecting the request —
// mirrors backend/app/core/auth.py's optional_firebase_token. Unused today
// (no commerce route needs guest-or-authed semantics yet) but kept alongside
// requireFirebaseAuth since it's the same verification path.
func optionalFirebaseAuth(client *fbauth.Client) middleware {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if raw, ok := bearerToken(r); ok {
				if token, err := client.VerifyIDToken(r.Context(), raw); err == nil {
					r = r.WithContext(context.WithValue(r.Context(), firebaseTokenContextKey, token))
				}
			}
			next.ServeHTTP(w, r)
		})
	}
}

// ---------------------------------------------------------------------------
// CORS
// ---------------------------------------------------------------------------

// cors mirrors backend/app/main.py's CORSMiddleware config: only the
// configured origins are echoed back, credentials are allowed, and any
// method/header is permitted.
func cors(origins []string) middleware {
	allowed := make(map[string]bool, len(origins))
	for _, o := range origins {
		allowed[o] = true
	}
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			origin := r.Header.Get("Origin")
			if allowed[origin] {
				w.Header().Set("Access-Control-Allow-Origin", origin)
				w.Header().Set("Access-Control-Allow-Credentials", "true")
				w.Header().Set("Vary", "Origin")
			}
			if r.Method == http.MethodOptions {
				w.Header().Set("Access-Control-Allow-Methods", "*")
				w.Header().Set("Access-Control-Allow-Headers", "*")
				w.WriteHeader(http.StatusNoContent)
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}
