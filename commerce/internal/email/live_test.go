package email

import (
	"os"
	"testing"
)

// TestSendLive actually hits the Resend API. It is skipped unless
// RESEND_LIVE_TO names a recipient, so CI and `go test ./...` stay offline:
//
//	RESEND_API_KEY=re_... RESEND_LIVE_TO=you@example.com \
//	  go test ./internal/email -run TestSendLive -v
//
// Use it to confirm the API key, the verified from-domain, and how the
// templates actually render in a mail client — the things a unit test on the
// builders can't tell you.
func TestSendLive(t *testing.T) {
	to := os.Getenv("RESEND_LIVE_TO")
	if to == "" {
		t.Skip("set RESEND_LIVE_TO to send a real email")
	}
	from := os.Getenv("EMAIL_FROM")
	if from == "" {
		from = "Palladium <noreply@palladiumtech.ai>"
	}
	c := New(os.Getenv("RESEND_API_KEY"), from)
	if !c.Enabled() {
		t.Fatal("RESEND_API_KEY is empty — nothing would be sent")
	}

	builders := map[string]func() (Message, error){
		"welcome": func() (Message, error) { return WelcomeMessage(to) },
	}
	if os.Getenv("RESEND_LIVE_ALL") != "" {
		builders["account deleted"] = func() (Message, error) { return AccountDeletedMessage(to) }
		builders["price alert"] = func() (Message, error) {
			return PriceAlertMessage(PriceAlert{
				Email:       to,
				PartName:    "AMD Ryzen 7 9800X3D",
				Marketplace: "Amazon",
				OldCents:    54999,
				NewCents:    42950,
				Currency:    "USD",
				URL:         "https://palladiumtech.ai",
			})
		}
	}

	for name, build := range builders {
		t.Run(name, func(t *testing.T) {
			msg, err := build()
			if err != nil {
				t.Fatalf("build: %v", err)
			}
			if err := c.Send(t.Context(), msg); err != nil {
				t.Fatalf("send: %v", err)
			}
			t.Logf("sent %q to %s from %s", msg.Subject, to, from)
		})
	}
}
