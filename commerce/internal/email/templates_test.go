package email

import (
	"strings"
	"testing"
)

// The builders must render their embedded templates without error and
// address the right recipient — a broken template should fail in CI, not on
// the first signup in prod.
func TestMessageBuilders(t *testing.T) {
	tests := []struct {
		name    string
		build   func(string) (Message, error)
		wantSub string
	}{
		{"welcome", WelcomeMessage, "Welcome to Palladium"},
		{"account deleted", AccountDeletedMessage, "Your Palladium account has been deleted"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			msg, err := tt.build("wade@example.com")
			if err != nil {
				t.Fatalf("build: %v", err)
			}
			if msg.To != "wade@example.com" {
				t.Errorf("To = %q, want wade@example.com", msg.To)
			}
			if msg.Subject != tt.wantSub {
				t.Errorf("Subject = %q, want %q", msg.Subject, tt.wantSub)
			}
			if !strings.Contains(msg.HTML, "wade@example.com") {
				t.Errorf("HTML does not mention the recipient address")
			}
			if msg.Text == "" {
				t.Errorf("missing plain-text fallback")
			}
		})
	}
}

// Send must be a silent no-op with no API key — the prod path when the
// resend-api-key secret is absent or empty.
func TestSendDisabled(t *testing.T) {
	c := New("", "Palladium <noreply@palladiumtech.ai>")
	if c.Enabled() {
		t.Fatal("client with empty key reports Enabled")
	}
	if err := c.Send(t.Context(), Message{To: "x@example.com", Subject: "s"}); err != nil {
		t.Fatalf("disabled Send returned error: %v", err)
	}
}
