package email

import (
	"embed"
	"fmt"
	"html/template"
	"strconv"
	"strings"
)

// Templates are embedded at build time so the binary stays self-contained
// (the Docker image is distroless, so there is no filesystem to read from).
// To add a draft: drop an .html file in templates/ defining "preheader" and
// "content", add a pageTemplate for it below, add a builder.
//
//go:embed templates/*.html
var templateFS embed.FS

// pageTemplate is one message body: layout.html plus a single page file.
//
// Parsed per page rather than as one shared set on purpose. Every page defines
// blocks under the same names ("content", "preheader"), so a single set built
// from templates/*.html would silently keep only whichever file parsed last
// and send that body for every message.
type pageTemplate struct {
	name string
	tmpl *template.Template
}

func newPage(name string) pageTemplate {
	return pageTemplate{
		name: name,
		tmpl: template.Must(template.ParseFS(templateFS, "templates/layout.html", "templates/"+name)),
	}
}

func (p pageTemplate) render(data any) (string, error) {
	var buf strings.Builder
	if err := p.tmpl.ExecuteTemplate(&buf, "layout", data); err != nil {
		return "", fmt.Errorf("render email template %s: %w", p.name, err)
	}
	return buf.String(), nil
}

var (
	welcomePage        = newPage("welcome.html")
	accountDeletedPage = newPage("account_deleted.html")
	priceAlertPage     = newPage("price_alert.html")
)

// WelcomeMessage greets a newly created account.
func WelcomeMessage(to string) (Message, error) {
	subject := "Welcome to Palladium"
	html, err := welcomePage.render(struct {
		Subject string
		Email   string
	}{subject, to})
	if err != nil {
		return Message{}, err
	}
	return Message{
		To:      to,
		Subject: subject,
		HTML:    html,
		Text: "Welcome to Palladium!\n\n" +
			"Your account is ready. Start a build and we'll walk you through " +
			"picking every part: https://palladiumtech.ai/build/new",
	}, nil
}

// AccountDeletedMessage confirms an account deletion.
func AccountDeletedMessage(to string) (Message, error) {
	subject := "Your Palladium account has been deleted"
	html, err := accountDeletedPage.render(struct {
		Subject string
		Email   string
	}{subject, to})
	if err != nil {
		return Message{}, err
	}
	return Message{
		To:      to,
		Subject: subject,
		HTML:    html,
		Text: "Your Palladium account and its data have been permanently deleted.\n\n" +
			"If this wasn't you, contact us right away by replying to this email.",
	}, nil
}

// PriceAlert describes a price drop on a tracked part.
//
// Prices are minor units (cents) to match store.Listing.PriceAmount, so
// callers pass what the database holds and never do the conversion, and
// Currency is the ISO code from the same row (empty means USD).
//
// Nothing sends these yet: the template and builder exist so the alerting job,
// when it lands, has a message to hand to Client.Send.
type PriceAlert struct {
	Email       string
	PartName    string
	Marketplace string // "Amazon", "eBay"
	OldCents    int64
	NewCents    int64
	Currency    string
	URL         string // listing URL, affiliate tag already applied
}

// PriceAlertMessage announces a price drop. It rejects a non-drop rather than
// rendering "Down $0.00": a caller passing prices in the wrong order, or an
// alerting job with an off-by-one in its comparison, should fail loudly here
// instead of mailing users nonsense.
func PriceAlertMessage(a PriceAlert) (Message, error) {
	if a.NewCents >= a.OldCents {
		return Message{}, fmt.Errorf("price alert for %q is not a drop: %d -> %d", a.PartName, a.OldCents, a.NewCents)
	}

	newPrice := formatMoney(a.NewCents, a.Currency)
	oldPrice := formatMoney(a.OldCents, a.Currency)
	savings := formatMoney(a.OldCents-a.NewCents, a.Currency)

	// Guarded because a drop from a free or bad zero price would divide by
	// zero; the template omits the parenthetical when this is empty.
	percentOff := ""
	if a.OldCents > 0 {
		percentOff = strconv.FormatInt((a.OldCents-a.NewCents)*100/a.OldCents, 10) + "% off"
	}

	subject := fmt.Sprintf("Price drop: %s is now %s", a.PartName, newPrice)
	html, err := priceAlertPage.render(struct {
		Subject     string
		Email       string
		PartName    string
		Marketplace string
		NewPrice    string
		OldPrice    string
		Savings     string
		PercentOff  string
		URL         string
	}{subject, a.Email, a.PartName, a.Marketplace, newPrice, oldPrice, savings, percentOff, a.URL})
	if err != nil {
		return Message{}, err
	}

	text := fmt.Sprintf("%s dropped to %s at %s (was %s, down %s).",
		a.PartName, newPrice, a.Marketplace, oldPrice, savings)
	if a.URL != "" {
		text += "\n\nView the listing: " + a.URL
	}

	return Message{To: a.Email, Subject: subject, HTML: html, Text: text}, nil
}

// formatMoney renders minor units as a display price. Currencies with a
// symbol get it prefixed; anything else is prefixed with its ISO code, which
// is wrong for no locale in a way that misleads about the amount.
func formatMoney(cents int64, currency string) string {
	symbols := map[string]string{"USD": "$", "CAD": "$", "GBP": "£", "EUR": "€", "": "$"}

	whole := cents / 100
	frac := cents % 100
	if frac < 0 {
		frac = -frac
	}
	amount := addThousands(whole) + "." + fmt.Sprintf("%02d", frac)

	if sym, ok := symbols[strings.ToUpper(currency)]; ok {
		return sym + amount
	}
	return strings.ToUpper(currency) + " " + amount
}

// addThousands groups an integer with commas: 1234567 becomes "1,234,567".
func addThousands(n int64) string {
	s := strconv.FormatInt(n, 10)
	sign := ""
	if strings.HasPrefix(s, "-") {
		sign, s = "-", s[1:]
	}
	for i := len(s) - 3; i > 0; i -= 3 {
		s = s[:i] + "," + s[i:]
	}
	return sign + s
}
