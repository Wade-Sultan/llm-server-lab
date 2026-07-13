package email

import (
	"embed"
	"fmt"
	"html/template"
	"strings"
)

// Templates are embedded at build time so the binary stays self-contained
// (the Docker image is distroless — no filesystem to read from). To add a
// draft: drop an .html file in templates/, add a builder below.
//
//go:embed templates/*.html
var templateFS embed.FS

var templates = template.Must(template.ParseFS(templateFS, "templates/*.html"))

func render(name string, data any) (string, error) {
	var buf strings.Builder
	if err := templates.ExecuteTemplate(&buf, name, data); err != nil {
		return "", fmt.Errorf("render email template %s: %w", name, err)
	}
	return buf.String(), nil
}

// WelcomeMessage greets a newly created account.
func WelcomeMessage(to string) (Message, error) {
	html, err := render("welcome.html", map[string]string{"Email": to})
	if err != nil {
		return Message{}, err
	}
	return Message{
		To:      to,
		Subject: "Welcome to Palladium",
		HTML:    html,
		Text: "Welcome to Palladium!\n\n" +
			"Your account is ready. Start a build and we'll walk you through " +
			"picking every part: https://palladiumtech.ai/build/new",
	}, nil
}

// AccountDeletedMessage confirms an account deletion.
func AccountDeletedMessage(to string) (Message, error) {
	html, err := render("account_deleted.html", map[string]string{"Email": to})
	if err != nil {
		return Message{}, err
	}
	return Message{
		To:      to,
		Subject: "Your Palladium account has been deleted",
		HTML:    html,
		Text: "Your Palladium account and its data have been permanently deleted.\n\n" +
			"If this wasn't you, contact us right away by replying to this email.",
	}, nil
}
