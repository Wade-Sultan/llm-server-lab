package server

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"time"

	"github.com/palladium/commerce/internal/email"
	"github.com/palladium/commerce/internal/store"
)

// The service-to-service surface. Today it is one route: the builder's pricing
// ETL asking commerce to mail a customer about a price drop.
//
// Commerce sends because it is the only service holding RESEND_API_KEY and the
// message templates (see deploy/overlays/prod/patches/secrets-scoped.yaml).
// The caller passes a user id, never an address — users.email is resolved here,
// from the row that owns it.

// priceAlertRequest is what the builder POSTs. Money is in minor units
// throughout, matching both store.Listing.PriceAmount and the builder's own
// *_cents columns, so no conversion happens in transit.
type priceAlertRequest struct {
	UserID   string `json:"user_id"`
	PartName string `json:"part_name"`
	OldCents int64  `json:"old_cents"`
	NewCents int64  `json:"new_cents"`
	Currency string `json:"currency"`

	// Both optional. The ETL's price is a median across retailers rather than
	// one listing, so it has neither; an alert sourced from a specific listing
	// would supply both and get the "at <retailer>" clause and the CTA button.
	Marketplace string `json:"marketplace"`
	URL         string `json:"url"`
}

// sendPriceAlert mails one customer about one price drop.
//
// Synchronous, unlike the fire-and-forget welcome/deletion mail in
// sendEmailAsync: the caller retires a price subscription on the strength of
// this response, so "accepted" has to mean the message actually went to Resend.
// A 2xx here is what stops the alert from firing again next run, and anything
// else leaves the subscription active to be retried.
func (h *handlers) sendPriceAlert(w http.ResponseWriter, r *http.Request) {
	var req priceAlertRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid JSON body"})
		return
	}

	switch {
	case req.UserID == "":
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": "user_id is required"})
		return
	case req.PartName == "":
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": "part_name is required"})
		return
	case req.NewCents >= req.OldCents:
		// email.PriceAlertMessage refuses this too; rejecting it here makes it
		// a clear 422 for the caller rather than an opaque render failure.
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": "new_cents must be below old_cents"})
		return
	}

	// Checked before the lookup: with no Resend key, Client.Send is a no-op
	// that returns nil, so reporting success would retire a subscription whose
	// mail was never sent.
	if !h.email.Enabled() {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "email delivery is not configured"})
		return
	}

	user, err := h.store.GetUserByID(r.Context(), req.UserID)
	switch {
	case errors.Is(err, store.ErrNotFound):
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "user not found"})
		return
	case err != nil:
		h.logger.Error("price alert: look up user", "err", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "internal server error"})
		return
	}
	// A deactivated account still has a deliverable address, which is exactly
	// why this is checked: it must not receive marketing-adjacent mail.
	if !user.IsActive {
		writeJSON(w, http.StatusConflict, map[string]string{"error": "account is not active"})
		return
	}

	msg, err := email.PriceAlertMessage(email.PriceAlert{
		Email:       user.Email,
		PartName:    req.PartName,
		Marketplace: req.Marketplace,
		OldCents:    req.OldCents,
		NewCents:    req.NewCents,
		Currency:    req.Currency,
		URL:         req.URL,
	})
	if err != nil {
		h.logger.Error("price alert: build message", "err", err)
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": err.Error()})
		return
	}

	// Bounded independently of the caller's own timeout so a slow Resend can't
	// pin this handler for as long as the client is willing to wait.
	ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
	defer cancel()
	if err := h.email.Send(ctx, msg); err != nil {
		h.logger.Error("price alert: send", "to", user.Email, "err", err)
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": "failed to send email"})
		return
	}

	h.logger.Info("price alert sent", "user_id", user.ID, "part", req.PartName)
	writeJSON(w, http.StatusOK, map[string]string{"status": "sent"})
}
