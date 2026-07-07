package server

import (
	"encoding/json"
	"errors"
	"net/http"
	"strconv"
	"time"

	"github.com/palladium/commerce/internal/listings"
	"github.com/palladium/commerce/internal/store"
)

// listingDTO mirrors backend/app/schemas/listing.py's ListingPublic /
// AmazonListingPublic. Only the "amazon" listing_type exists in practice
// today, so Amazon fields are just plain (omitempty) fields here rather than
// a discriminated union.
type listingDTO struct {
	ID                 string     `json:"id"`
	PartID             string     `json:"part_id"`
	ListingType        string     `json:"listing_type"`
	Marketplace        string     `json:"marketplace"`
	URL                *string    `json:"url"`
	PriceAmount        *int64     `json:"price_amount"`
	Currency           *string    `json:"currency"`
	ImageURL           *string    `json:"image_url"`
	IsActive           bool       `json:"is_active"`
	FetchedAt          *time.Time `json:"fetched_at"`
	PriceLastUpdatedAt *time.Time `json:"price_last_updated_at"`
	CreatedAt          time.Time  `json:"created_at"`
	UpdatedAt          time.Time  `json:"updated_at"`

	ASIN         *string `json:"asin,omitempty"`
	Brand        *string `json:"brand,omitempty"`
	IsPrime      *bool   `json:"is_prime,omitempty"`
	SellerName   *string `json:"seller_name,omitempty"`
	AffiliateTag *string `json:"affiliate_tag,omitempty"`
}

type listingsResponse struct {
	Data  []listingDTO `json:"data"`
	Count int          `json:"count"`
}

// toDTO serializes a store.Listing, applying listings.AmazonURL to fill in a
// working affiliate link even when the stored url column is empty — a
// deliberate parity improvement over today's Python route (which returns the
// raw stored url as-is), matching what the chat pipeline already does
// internally for recommended builds (crud/reference_builds.py).
func (h *handlers) toDTO(l *store.Listing) listingDTO {
	dto := listingDTO{
		ID: l.ID, PartID: l.PartID, ListingType: l.ListingType, Marketplace: l.Marketplace,
		URL: l.URL, PriceAmount: l.PriceAmount, Currency: l.Currency, ImageURL: l.ImageURL,
		IsActive: l.IsActive, FetchedAt: l.FetchedAt, PriceLastUpdatedAt: l.PriceLastUpdatedAt,
		CreatedAt: l.CreatedAt, UpdatedAt: l.UpdatedAt,
		ASIN: l.ASIN, Brand: l.Brand, IsPrime: l.IsPrime, SellerName: l.SellerName, AffiliateTag: l.AffiliateTag,
	}
	if l.ListingType == "amazon" && l.ASIN != nil {
		url := listings.AmazonURL(l.URL, *l.ASIN, h.associateTag)
		dto.URL = &url
	}
	return dto
}

func parseListingFilter(r *http.Request) store.ListingFilter {
	q := r.URL.Query()
	f := store.ListingFilter{Skip: 0, Limit: 100}
	if v := q.Get("part_id"); v != "" {
		f.PartID = &v
	}
	if v := q.Get("marketplace"); v != "" {
		f.Marketplace = &v
	}
	if v := q.Get("skip"); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			f.Skip = n
		}
	}
	if v := q.Get("limit"); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			f.Limit = n
		}
	}
	return f
}

func (h *handlers) listListings(w http.ResponseWriter, r *http.Request) {
	filter := parseListingFilter(r)

	rows, err := h.store.ListListings(r.Context(), filter)
	if err != nil {
		h.logger.Error("list listings", "err", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "internal server error"})
		return
	}
	count, err := h.store.CountListings(r.Context(), filter)
	if err != nil {
		h.logger.Error("count listings", "err", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "internal server error"})
		return
	}

	dtos := make([]listingDTO, 0, len(rows))
	for _, l := range rows {
		dtos = append(dtos, h.toDTO(l))
	}
	writeJSON(w, http.StatusOK, listingsResponse{Data: dtos, Count: count})
}

func (h *handlers) getListing(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	l, err := h.store.GetListingByID(r.Context(), id)
	if errors.Is(err, store.ErrNotFound) {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "Listing not found"})
		return
	}
	if err != nil {
		h.logger.Error("get listing", "err", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "internal server error"})
		return
	}
	writeJSON(w, http.StatusOK, h.toDTO(l))
}

func (h *handlers) getListingsByPart(w http.ResponseWriter, r *http.Request) {
	partID := r.PathValue("part_id")

	exists, err := h.store.PartExists(r.Context(), partID)
	if err != nil {
		h.logger.Error("check part exists", "err", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "internal server error"})
		return
	}
	if !exists {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "PCPart not found"})
		return
	}

	rows, err := h.store.GetListingsByPartID(r.Context(), partID)
	if err != nil {
		h.logger.Error("get listings by part", "err", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "internal server error"})
		return
	}
	dtos := make([]listingDTO, 0, len(rows))
	for _, l := range rows {
		dtos = append(dtos, h.toDTO(l))
	}
	writeJSON(w, http.StatusOK, listingsResponse{Data: dtos, Count: len(dtos)})
}

// createListingRequest mirrors schemas/listing.py's AmazonListingCreate — the
// only listing_type actually created in practice today.
type createListingRequest struct {
	PartID       string  `json:"part_id"`
	Marketplace  string  `json:"marketplace"`
	URL          *string `json:"url"`
	PriceAmount  *int64  `json:"price_amount"`
	Currency     *string `json:"currency"`
	ImageURL     *string `json:"image_url"`
	ASIN         string  `json:"asin"`
	Brand        *string `json:"brand"`
	IsPrime      *bool   `json:"is_prime"`
	SellerName   *string `json:"seller_name"`
	AffiliateTag *string `json:"affiliate_tag"`
}

func (h *handlers) createListing(w http.ResponseWriter, r *http.Request) {
	var req createListingRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid request body"})
		return
	}
	if req.PartID == "" || req.Marketplace == "" || req.ASIN == "" {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": "part_id, marketplace, and asin are required"})
		return
	}

	l, err := h.store.CreateListing(r.Context(), store.CreateListingInput{
		PartID: req.PartID, Marketplace: req.Marketplace, URL: req.URL,
		PriceAmount: req.PriceAmount, Currency: req.Currency, ImageURL: req.ImageURL,
		ASIN: req.ASIN, Brand: req.Brand, IsPrime: req.IsPrime,
		SellerName: req.SellerName, AffiliateTag: req.AffiliateTag,
	})
	if errors.Is(err, store.ErrConflict) {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "part_id not found"})
		return
	}
	if err != nil {
		h.logger.Error("create listing", "err", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "internal server error"})
		return
	}
	writeJSON(w, http.StatusCreated, h.toDTO(l))
}

// updateListingRequest mirrors schemas/listing.py's ListingUpdate — all
// fields optional, only non-nil ones are applied.
type updateListingRequest struct {
	URL                *string    `json:"url"`
	PriceAmount        *int64     `json:"price_amount"`
	Currency           *string    `json:"currency"`
	ImageURL           *string    `json:"image_url"`
	IsActive           *bool      `json:"is_active"`
	FetchedAt          *time.Time `json:"fetched_at"`
	PriceLastUpdatedAt *time.Time `json:"price_last_updated_at"`
}

func (h *handlers) updateListing(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	var req updateListingRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid request body"})
		return
	}

	l, err := h.store.UpdateListing(r.Context(), id, store.UpdateListingInput{
		URL: req.URL, PriceAmount: req.PriceAmount, Currency: req.Currency,
		ImageURL: req.ImageURL, IsActive: req.IsActive,
		FetchedAt: req.FetchedAt, PriceLastUpdatedAt: req.PriceLastUpdatedAt,
	})
	if errors.Is(err, store.ErrNotFound) {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "Listing not found"})
		return
	}
	if err != nil {
		h.logger.Error("update listing", "err", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "internal server error"})
		return
	}
	writeJSON(w, http.StatusOK, h.toDTO(l))
}

func (h *handlers) deleteListing(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	err := h.store.DeleteListing(r.Context(), id)
	if errors.Is(err, store.ErrNotFound) {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "Listing not found"})
		return
	}
	if err != nil {
		h.logger.Error("delete listing", "err", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "internal server error"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"message": "Listing deleted successfully"})
}
