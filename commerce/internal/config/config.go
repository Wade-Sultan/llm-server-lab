// Package config loads runtime configuration from the environment.
//
// Load fails fast: a misconfigured instance should refuse to start rather
// than surface errors on the first request. As the service grows, required
// values (database DSN, Firebase project ID) are validated here.
package config

import (
	"fmt"
	"os"
	"strings"
)

// Config holds all runtime configuration.
type Config struct {
	// Port is the TCP port to listen on. Cloud Run injects PORT (default 8080).
	Port string

	// MetricsPort is the TCP port serving /metrics for Google Managed
	// Prometheus. Deliberately separate from Port and absent from the k8s
	// Service: both HTTPRoutes match every path, so /metrics on the API port
	// would be public. GMP scrapes pod IPs directly, bypassing Service and
	// Gateway, so a pod-only port is scrapeable without being routable.
	MetricsPort string

	// Env identifies the deployment environment, e.g. "dev" or "prod".
	Env string

	// DatabaseURL, when set, is a plain Postgres DSN (e.g.
	// "postgresql://user:pass@localhost:5433/palladium") used for local dev via
	// the cloud-sql-proxy — mirroring admin's DATABASE_URL. It bypasses the
	// Cloud SQL connector + IAM auth, so the connector-only vars below
	// (InstanceConnectionName, DBIAMUser, DBName) aren't required when it's set.
	DatabaseURL string

	// InstanceConnectionName is the Cloud SQL instance "project:region:instance".
	InstanceConnectionName string

	// DBIAMUser is the IAM database user (service account email minus the
	// ".gserviceaccount.com" suffix), e.g. "commerce-sa@proj.iam".
	DBIAMUser string

	// DBName is the target database name.
	DBName string

	// DBUsePrivateIP routes DB traffic over the instance's private IP.
	DBUsePrivateIP bool

	// AssociateTag is the Amazon Associates tracking id for affiliate links.
	AssociateTag string

	// EbayCampaignID is the eBay Partner Network campaign id (campid) appended
	// to eBay listing URLs for click attribution. Empty leaves links working
	// but unattributed, mirroring AssociateTag.
	EbayCampaignID string

	// FirebaseProjectID is passed to the Firebase Admin SDK for ID token
	// verification. Required outside dev, where Application Default
	// Credentials on Cloud Run resolve the project automatically.
	FirebaseProjectID string

	// FrontendHost is the primary frontend origin, always allowed by CORS
	// (mirrors backend/app/core/config.py's FRONTEND_HOST/all_cors_origins).
	FrontendHost string

	// CORSOrigins is the full set of allowed CORS origins, including
	// FrontendHost.
	CORSOrigins []string

	// ResendAPIKey configures internal/email's Resend client. Empty disables
	// email sending entirely (it's not required for anything today).
	ResendAPIKey string
	EmailFrom    string
}

// Load reads configuration from the environment and validates it. Required
// values are checked here so a misconfigured instance refuses to start.
func Load() (*Config, error) {
	cfg := &Config{
		Port:                   getenv("PORT", "8080"),
		MetricsPort:            getenv("METRICS_PORT", "9090"),
		Env:                    getenv("ENV", "dev"),
		DatabaseURL:            os.Getenv("DATABASE_URL"),
		InstanceConnectionName: os.Getenv("INSTANCE_CONNECTION_NAME"),
		DBIAMUser:              os.Getenv("DB_IAM_USER"),
		DBName:                 os.Getenv("DB_NAME"),
		DBUsePrivateIP:         os.Getenv("DB_USE_PRIVATE_IP") == "true",
		AssociateTag:           os.Getenv("AMAZON_ASSOCIATE_TAG"),
		EbayCampaignID:         os.Getenv("EBAY_CAMPAIGN_ID"),
		FirebaseProjectID:      os.Getenv("FIREBASE_PROJECT_ID"),
		FrontendHost:           getenv("FRONTEND_HOST", "http://localhost:3000"),
		ResendAPIKey:           os.Getenv("RESEND_API_KEY"),
		EmailFrom:              os.Getenv("EMAIL_FROM"),
	}
	cfg.CORSOrigins = append(parseCORSOrigins(os.Getenv("BACKEND_CORS_ORIGINS")), strings.TrimRight(cfg.FrontendHost, "/"))

	var missing []string
	// The Cloud SQL connector vars are only needed when DATABASE_URL isn't
	// set (i.e. prod / connector path). A plain DATABASE_URL supplies host,
	// user, and dbname itself.
	if cfg.DatabaseURL == "" {
		for k, v := range map[string]string{
			"INSTANCE_CONNECTION_NAME": cfg.InstanceConnectionName,
			"DB_IAM_USER":              cfg.DBIAMUser,
			"DB_NAME":                  cfg.DBName,
		} {
			if v == "" {
				missing = append(missing, k)
			}
		}
	}
	if cfg.Env != "dev" && cfg.FirebaseProjectID == "" {
		missing = append(missing, "FIREBASE_PROJECT_ID")
	}
	if len(missing) > 0 {
		return nil, fmt.Errorf("missing required env vars: %v", missing)
	}

	return cfg, nil
}

// parseCORSOrigins splits a comma-separated env var into a trimmed origin list.
func parseCORSOrigins(v string) []string {
	if v == "" {
		return nil
	}
	parts := strings.Split(v, ",")
	origins := make([]string, 0, len(parts))
	for _, p := range parts {
		p = strings.TrimSpace(p)
		if p == "" {
			continue
		}
		origins = append(origins, strings.TrimRight(p, "/"))
	}
	return origins
}

// getenv returns the value of key, or fallback if unset/empty.
func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
