// Package email sends transactional email via Resend (https://resend.com).
//
// Nothing in commerce currently requires this: password reset is handled
// entirely client-side by Firebase (frontend/src/hooks/useAuth.ts's
// resetPassword), and there's no other transactional email need yet. This
// client is a stub for future use (e.g. an account-deleted confirmation) —
// it's inert whenever APIKey is empty, and no caller wires it up yet.
package email

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
)

const apiURL = "https://api.resend.com/emails"

type Client struct {
	apiKey string
	from   string
	http   *http.Client
}

// New returns a Client. If apiKey is empty, Send is a no-op — callers don't
// need to branch on whether email is configured.
func New(apiKey, from string) *Client {
	return &Client{apiKey: apiKey, from: from, http: &http.Client{}}
}

func (c *Client) Enabled() bool {
	return c.apiKey != ""
}

// Send fires a plain-text transactional email. Best-effort: callers should
// treat a returned error as non-fatal to whatever request triggered it.
func (c *Client) Send(ctx context.Context, to, subject, body string) error {
	if !c.Enabled() {
		return nil
	}

	payload, err := json.Marshal(map[string]any{
		"from":    c.from,
		"to":      []string{to},
		"subject": subject,
		"text":    body,
	})
	if err != nil {
		return fmt.Errorf("marshal resend payload: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, apiURL, bytes.NewReader(payload))
	if err != nil {
		return fmt.Errorf("build resend request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+c.apiKey)
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("send resend request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 300 {
		return fmt.Errorf("resend returned status %d", resp.StatusCode)
	}
	return nil
}
