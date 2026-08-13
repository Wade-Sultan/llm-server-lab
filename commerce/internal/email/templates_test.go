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

// sampleAlert is a representative drop: $549.99 to $429.50.
func sampleAlert() PriceAlert {
	return PriceAlert{
		Email:       "wade@example.com",
		PartName:    "AMD Ryzen 7 9800X3D",
		Marketplace: "Amazon",
		OldCents:    54999,
		NewCents:    42950,
		Currency:    "USD",
		URL:         "https://example.com/listing/1",
	}
}

// allMessages renders one of every message so branding assertions cover the
// whole set rather than whichever templates someone remembered to add.
func allMessages(t *testing.T) map[string]Message {
	t.Helper()
	out := map[string]Message{}
	for name, build := range map[string]func(string) (Message, error){
		"welcome":         WelcomeMessage,
		"account deleted": AccountDeletedMessage,
	} {
		msg, err := build("wade@example.com")
		if err != nil {
			t.Fatalf("build %s: %v", name, err)
		}
		out[name] = msg
	}
	msg, err := PriceAlertMessage(sampleAlert())
	if err != nil {
		t.Fatalf("build price alert: %v", err)
	}
	out["price alert"] = msg
	return out
}

// Every message carries the site's branding: the logo, Raleway for headings,
// DM Sans for body copy. Asserted centrally so a new template can't quietly
// ship with the old Helvetica-only styling.
func TestBranding(t *testing.T) {
	const logoURL = "https://palladiumtech.ai/assets/images/palladium-logo-main.png"
	for name, msg := range allMessages(t) {
		t.Run(name, func(t *testing.T) {
			for _, want := range []string{logoURL, "Raleway", "DM Sans", "fonts.googleapis.com"} {
				if !strings.Contains(msg.HTML, want) {
					t.Errorf("HTML is missing %q", want)
				}
			}
			// Webfonts are stripped by Gmail and Outlook, so every stack must
			// carry a fallback the client actually has.
			if strings.Count(msg.HTML, "'Raleway'") != strings.Count(msg.HTML, "'Raleway', 'Helvetica Neue', Helvetica, Arial, sans-serif") {
				t.Error("a Raleway font-family is missing its fallback stack")
			}
			if strings.Count(msg.HTML, "'DM Sans'") != strings.Count(msg.HTML, "'DM Sans', 'Helvetica Neue', Helvetica, Arial, sans-serif") {
				t.Error("a DM Sans font-family is missing its fallback stack")
			}
		})
	}
}

// House style: no em-dashes in customer-facing copy.
func TestNoEmDashes(t *testing.T) {
	for name, msg := range allMessages(t) {
		t.Run(name, func(t *testing.T) {
			for _, bad := range []string{"—", "&mdash;", "&#8212;"} {
				if strings.Contains(msg.HTML, bad) {
					t.Errorf("HTML contains an em-dash (%s)", bad)
				}
			}
			if strings.Contains(msg.Text, "—") {
				t.Error("plain-text body contains an em-dash")
			}
		})
	}
}

// Each page must render its own body. Parsing every template into one shared
// set would leave all three sharing the last-parsed "content" block, and the
// symptom would be three identical emails.
func TestPagesRenderDistinctBodies(t *testing.T) {
	msgs := allMessages(t)
	markers := map[string]string{
		"welcome":         "Welcome to Palladium!",
		"account deleted": "Your account has been deleted",
		"price alert":     "A part in your build got cheaper",
	}
	for name, marker := range markers {
		if !strings.Contains(msgs[name].HTML, marker) {
			t.Errorf("%s is missing its own heading %q", name, marker)
		}
		for other, otherMarker := range markers {
			if other != name && strings.Contains(msgs[name].HTML, otherMarker) {
				t.Errorf("%s leaked %s's heading", name, other)
			}
		}
	}
}

func TestPriceAlertMessage(t *testing.T) {
	msg, err := PriceAlertMessage(sampleAlert())
	if err != nil {
		t.Fatalf("build: %v", err)
	}
	if msg.To != "wade@example.com" {
		t.Errorf("To = %q", msg.To)
	}
	if want := "Price drop: AMD Ryzen 7 9800X3D is now $429.50"; msg.Subject != want {
		t.Errorf("Subject = %q, want %q", msg.Subject, want)
	}
	// New price, old price, absolute saving and percentage all shown.
	for _, want := range []string{"$429.50", "$549.99", "$120.49", "21% off", "https://example.com/listing/1"} {
		if !strings.Contains(msg.HTML, want) {
			t.Errorf("HTML is missing %q", want)
		}
	}
	if !strings.Contains(msg.Text, "$429.50") || !strings.Contains(msg.Text, "https://example.com/listing/1") {
		t.Errorf("plain-text body = %q", msg.Text)
	}
}

// A drop is the only thing this message can truthfully say, so anything else
// must be an error rather than a rendered "Down $0.00".
func TestPriceAlertRejectsNonDrops(t *testing.T) {
	for _, tt := range []struct {
		name             string
		oldCents, newCts int64
	}{
		{"price rose", 42950, 54999},
		{"price unchanged", 54999, 54999},
	} {
		t.Run(tt.name, func(t *testing.T) {
			a := sampleAlert()
			a.OldCents, a.NewCents = tt.oldCents, tt.newCts
			if _, err := PriceAlertMessage(a); err == nil {
				t.Fatal("expected an error, got none")
			}
		})
	}
}

// Without a listing URL the call-to-action button must be omitted rather than
// rendered pointing at nothing.
func TestPriceAlertWithoutURL(t *testing.T) {
	a := sampleAlert()
	a.URL = ""
	msg, err := PriceAlertMessage(a)
	if err != nil {
		t.Fatalf("build: %v", err)
	}
	if strings.Contains(msg.HTML, "View the listing") {
		t.Error("rendered the CTA with no URL to point it at")
	}
	if strings.Contains(msg.Text, "View the listing") {
		t.Error("plain-text body offers a link that does not exist")
	}
}

// The pricing ETL's alerts have no marketplace — its price is a median across
// retailers — so the "at <retailer>" clause must disappear rather than render
// as a dangling "at ." in either body.
func TestPriceAlertWithoutMarketplace(t *testing.T) {
	a := sampleAlert()
	a.Marketplace = ""
	msg, err := PriceAlertMessage(a)
	if err != nil {
		t.Fatalf("build: %v", err)
	}
	for _, bad := range []string{" at .", "at  .", "dropped at"} {
		if strings.Contains(msg.HTML, bad) {
			t.Errorf("HTML contains a dangling clause %q", bad)
		}
		if strings.Contains(msg.Text, bad) {
			t.Errorf("plain-text body contains a dangling clause %q", bad)
		}
	}
	// First line only: the CTA line after it depends on URL, not marketplace.
	headline, _, _ := strings.Cut(msg.Text, "\n")
	if want := "AMD Ryzen 7 9800X3D dropped to $429.50 (was $549.99, down $120.49)."; headline != want {
		t.Errorf("Text headline = %q, want %q", headline, want)
	}
	// With one, it still reads the old way.
	withMarket, err := PriceAlertMessage(sampleAlert())
	if err != nil {
		t.Fatalf("build: %v", err)
	}
	if !strings.Contains(withMarket.Text, "at Amazon") {
		t.Errorf("named marketplace dropped out of the text body: %q", withMarket.Text)
	}
	if !strings.Contains(withMarket.HTML, "Amazon") {
		t.Error("named marketplace dropped out of the HTML body")
	}
}

func TestFormatMoney(t *testing.T) {
	tests := []struct {
		cents    int64
		currency string
		want     string
	}{
		{42950, "USD", "$429.50"},
		{42950, "", "$429.50"},       // empty currency defaults to USD
		{42950, "usd", "$429.50"},    // case-insensitive
		{99, "USD", "$0.99"},         // under a dollar
		{100000, "USD", "$1,000.00"}, // thousands separator
		{123456789, "USD", "$1,234,567.89"},
		{42950, "GBP", "£429.50"},
		{42950, "JPY", "JPY 429.50"}, // no symbol: prefix the ISO code
		{0, "USD", "$0.00"},
	}
	for _, tt := range tests {
		if got := formatMoney(tt.cents, tt.currency); got != tt.want {
			t.Errorf("formatMoney(%d, %q) = %q, want %q", tt.cents, tt.currency, got, tt.want)
		}
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
