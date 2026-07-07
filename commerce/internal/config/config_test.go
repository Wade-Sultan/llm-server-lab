package config

import (
	"os"
	"testing"
)

// clearEnv resets every var Load() reads, so tests don't leak state between
// each other or pick up the real developer's environment.
func clearEnv(t *testing.T) {
	t.Helper()
	vars := []string{
		"PORT", "ENV", "INSTANCE_CONNECTION_NAME", "DB_IAM_USER", "DB_NAME",
		"DB_USE_PRIVATE_IP", "AMAZON_ASSOCIATE_TAG", "FIREBASE_PROJECT_ID",
		"FRONTEND_HOST", "BACKEND_CORS_ORIGINS", "RESEND_API_KEY", "EMAIL_FROM",
	}
	for _, v := range vars {
		t.Setenv(v, "")
		os.Unsetenv(v)
	}
}

func TestLoad_MissingRequiredVars(t *testing.T) {
	clearEnv(t)

	_, err := Load()
	if err == nil {
		t.Fatal("expected error when required vars are unset, got nil")
	}
}

func TestLoad_DevDoesNotRequireFirebaseProjectID(t *testing.T) {
	clearEnv(t)
	t.Setenv("INSTANCE_CONNECTION_NAME", "proj:region:instance")
	t.Setenv("DB_IAM_USER", "commerce-sa@proj.iam")
	t.Setenv("DB_NAME", "palladium")
	// ENV left unset -> defaults to "dev"

	cfg, err := Load()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.Env != "dev" {
		t.Errorf("Env = %q, want %q", cfg.Env, "dev")
	}
}

func TestLoad_NonDevRequiresFirebaseProjectID(t *testing.T) {
	clearEnv(t)
	t.Setenv("INSTANCE_CONNECTION_NAME", "proj:region:instance")
	t.Setenv("DB_IAM_USER", "commerce-sa@proj.iam")
	t.Setenv("DB_NAME", "palladium")
	t.Setenv("ENV", "prod")

	_, err := Load()
	if err == nil {
		t.Fatal("expected error when FIREBASE_PROJECT_ID is unset outside dev, got nil")
	}
}

func TestLoad_CORSOriginsIncludesFrontendHost(t *testing.T) {
	clearEnv(t)
	t.Setenv("INSTANCE_CONNECTION_NAME", "proj:region:instance")
	t.Setenv("DB_IAM_USER", "commerce-sa@proj.iam")
	t.Setenv("DB_NAME", "palladium")
	t.Setenv("FRONTEND_HOST", "https://palladiumtech.ai/")
	t.Setenv("BACKEND_CORS_ORIGINS", "https://a.example.com/, https://b.example.com")

	cfg, err := Load()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	want := []string{"https://a.example.com", "https://b.example.com", "https://palladiumtech.ai"}
	if len(cfg.CORSOrigins) != len(want) {
		t.Fatalf("CORSOrigins = %v, want %v", cfg.CORSOrigins, want)
	}
	for i, o := range want {
		if cfg.CORSOrigins[i] != o {
			t.Errorf("CORSOrigins[%d] = %q, want %q", i, cfg.CORSOrigins[i], o)
		}
	}
}
