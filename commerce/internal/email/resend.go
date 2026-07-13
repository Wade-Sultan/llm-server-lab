// Package email sends transactional email via Resend (https://resend.com).
//
// Message content lives in templates/ (embedded at build time); the exported
// builders (WelcomeMessage, AccountDeletedMessage) pair a rendered template
// with its subject line so callers never assemble email content themselves.
// The client is inert whenever APIKey is empty — callers don't need to branch
// on whether email is configured.
package email

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
)

const apiURL = "https://api.resend.com/emails"

// Message is a single transactional email. HTML is the primary body; Text is
// the fallback for clients that don't render HTML.
type Message struct {
	To      string
	Subject string
	HTML    string
	Text    string
}

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

// Send fires a transactional email. Best-effort: callers should treat a
// returned error as non-fatal to whatever request triggered it.
func (c *Client) Send(ctx context.Context, msg Message) error {
	if !c.Enabled() {
		return nil
	}

	body := map[string]any{
		"from":    c.from,
		"to":      []string{msg.To},
		"subject": msg.Subject,
	}
	if msg.HTML != "" {
		body["html"] = msg.HTML
	}
	if msg.Text != "" {
		body["text"] = msg.Text
	}

	payload, err := json.Marshal(body)
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
