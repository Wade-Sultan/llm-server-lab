package server

import (
	"errors"
	"net/http"

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
	email, _ := token.Claims["email"].(string)

	user, err := h.store.GetUserByFirebaseUID(r.Context(), token.UID)
	switch {
	case errors.Is(err, store.ErrNotFound) && email != "":
		user, err = h.store.GetUserByEmail(r.Context(), email)
		switch {
		case errors.Is(err, store.ErrNotFound):
			user, err = h.store.CreateUser(r.Context(), token.UID, email)
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
		writeJSON(w, http.StatusOK, map[string]string{"message": "Account deleted successfully"})
	}
}
