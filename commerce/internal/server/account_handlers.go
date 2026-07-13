package server

import (
	"context"
	"errors"
	"net/http"
	"time"

	"github.com/palladium/commerce/internal/email"
	"github.com/palladium/commerce/internal/store"
)

type userDTO struct {
	ID          string  `json:"id"`
	FirebaseUID *string `json:"firebase_uid"`
	Email       string  `json:"email"`
	Username    *string `json:"username"`
	IsActive    bool    `json:"is_active"`
}

func userToDTO(u *store.User) userDTO {
	return userDTO{ID: u.ID, FirebaseUID: u.FirebaseUID, Email: u.Email, Username: u.Username, IsActive: u.IsActive}
}

// sendEmailAsync builds and sends a transactional email in the background.
// Best-effort by design: failures are logged, never surfaced to the request
// that triggered them, and the send outlives the request context (Cloud Run
// keeps the instance alive while the response is in flight; a lost email on
// instance reclaim is acceptable for these notification-only messages).
func (h *handlers) sendEmailAsync(build func(string) (email.Message, error), to string) {
	if !h.email.Enabled() || to == "" {
		return
	}
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		defer cancel()
		msg, err := build(to)
		if err == nil {
			err = h.email.Send(ctx, msg)
		}
		if err != nil {
			h.logger.Error("send email", "to", to, "err", err)
		}
	}()
}

// syncAccount creates or links the Postgres users row for the calling
// Firebase user, replacing backend/app/api/routes/users.py's /signup (which
// the frontend never actually called). Resolution order deliberately mirrors
// backend/app/api/routes/chat.py::_save_turn's get-by-uid -> get-by-email+link
// -> create logic, since both builder and commerce write to the same users
// table and must not diverge on how rows get created.
func (h *handlers) syncAccount(w http.ResponseWriter, r *http.Request) {
	token, ok := FirebaseTokenFromContext(r.Context())
	if !ok {
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "missing authentication token"})
		return
	}
	claimEmail, _ := token.Claims["email"].(string)

	created := false
	user, err := h.store.GetUserByFirebaseUID(r.Context(), token.UID)
	switch {
	case errors.Is(err, store.ErrNotFound) && claimEmail != "":
		user, err = h.store.GetUserByEmail(r.Context(), claimEmail)
		switch {
		case errors.Is(err, store.ErrNotFound):
			user, err = h.store.CreateUser(r.Context(), token.UID, claimEmail)
			created = err == nil
		case err == nil:
			user, err = h.store.LinkFirebaseUID(r.Context(), user.ID, token.UID)
		}
	case errors.Is(err, store.ErrNotFound):
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": "token has no email claim"})
		return
	}
	if err != nil {
		h.logger.Error("sync account", "err", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "internal server error"})
		return
	}

	// Welcome email only for genuinely new rows — not re-syncs or rows that
	// existed before (auto-provisioned by builder's chat pipeline) and just
	// got their firebase_uid linked.
	if created {
		h.sendEmailAsync(email.WelcomeMessage, user.Email)
	}
	writeJSON(w, http.StatusOK, userToDTO(user))
}

// deleteAccount replaces DELETE /users/me. Conversations cascade at the DB
// level; a user who still owns a PCBuild gets a clean 409 instead of the 500
// the old Python route would raise (pc_builds.owner_id has no ondelete
// clause) — a small, deliberate improvement bundled into this port.
func (h *handlers) deleteAccount(w http.ResponseWriter, r *http.Request) {
	token, ok := FirebaseTokenFromContext(r.Context())
	if !ok {
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "missing authentication token"})
		return
	}
	claimEmail, _ := token.Claims["email"].(string)

	err := h.store.DeleteUserByFirebaseUID(r.Context(), token.UID)
	switch {
	case errors.Is(err, store.ErrNotFound):
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "account not found"})
	case errors.Is(err, store.ErrConflict):
		writeJSON(w, http.StatusConflict, map[string]string{"error": "cannot delete account while builds exist"})
	case err != nil:
		h.logger.Error("delete account", "err", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "internal server error"})
	default:
		h.sendEmailAsync(email.AccountDeletedMessage, claimEmail)
		writeJSON(w, http.StatusOK, map[string]string{"message": "Account deleted successfully"})
	}
}
