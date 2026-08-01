package server

import (
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/prometheus/client_golang/prometheus"
)

// handlerLabels returns the set of `handler` label values currently recorded on
// http_requests_total, mapped to the status class each was seen with.
func handlerLabels(t *testing.T) map[string]string {
	t.Helper()

	families, err := prometheus.DefaultGatherer.Gather()
	if err != nil {
		t.Fatalf("gather: %v", err)
	}

	got := map[string]string{}
	for _, f := range families {
		if f.GetName() != "http_requests_total" {
			continue
		}
		for _, m := range f.GetMetric() {
			var handler, status string
			for _, l := range m.GetLabel() {
				switch l.GetName() {
				case "handler":
					handler = l.GetValue()
				case "status":
					status = l.GetValue()
				}
			}
			got[handler] = status
		}
	}
	return got
}

// The point of requestMetrics: many distinct URLs must collapse onto the route
// template, or every listing id becomes its own time series in GMP.
func TestRequestMetricsLabelsByRouteTemplate(t *testing.T) {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/v1/listings/{id}", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})

	h := requestMetrics(mux)(mux)

	for _, id := range []string{"a1b2", "c3d4", "e5f6"} {
		rr := httptest.NewRecorder()
		h.ServeHTTP(rr, httptest.NewRequest(http.MethodGet, "/api/v1/listings/"+id, nil))
		if rr.Code != http.StatusOK {
			t.Fatalf("got status %d, want 200", rr.Code)
		}
	}

	labels := handlerLabels(t)
	if _, ok := labels["/api/v1/listings/{id}"]; !ok {
		t.Errorf("no series for the route template; got handlers %v", keys(labels))
	}
	for h := range labels {
		if h == "/api/v1/listings/a1b2" || h == "/api/v1/listings/c3d4" {
			t.Errorf("resolved path leaked into the handler label: %q", h)
		}
	}
}

// Unmatched paths must share one bucket, so a scanner probing random URLs
// cannot mint unbounded series.
func TestRequestMetricsGroupsUnmatchedPaths(t *testing.T) {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {})

	h := requestMetrics(mux)(mux)

	for _, p := range []string{"/wp-admin", "/.env", "/phpmyadmin"} {
		rr := httptest.NewRecorder()
		h.ServeHTTP(rr, httptest.NewRequest(http.MethodGet, p, nil))
	}

	labels := handlerLabels(t)
	if status, ok := labels["other"]; !ok {
		t.Errorf(`no "other" bucket; got handlers %v`, keys(labels))
	} else if status != "4xx" {
		t.Errorf(`"other" recorded status %q, want "4xx"`, status)
	}
	for h := range labels {
		if h == "/wp-admin" || h == "/.env" {
			t.Errorf("unmatched path became its own series: %q", h)
		}
	}
}

// A panicking handler returns 500 to the client, so it must be counted as 5xx.
// This is what pins requestMetrics outside recoverPanic in server.New: wrapped
// the other way it would record the untouched 200 default and panics would
// read as successes on the dashboard.
func TestRequestMetricsRecordsPanicsAsServerErrors(t *testing.T) {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /boom", func(w http.ResponseWriter, r *http.Request) {
		panic("kaboom")
	})

	logger := slog.New(slog.NewJSONHandler(io.Discard, nil))
	h := requestMetrics(mux)(recoverPanic(logger)(mux))

	rr := httptest.NewRecorder()
	h.ServeHTTP(rr, httptest.NewRequest(http.MethodGet, "/boom", nil))

	if rr.Code != http.StatusInternalServerError {
		t.Fatalf("got status %d, want 500", rr.Code)
	}
	if status := handlerLabels(t)["/boom"]; status != "5xx" {
		t.Errorf("panic recorded as %q, want \"5xx\"", status)
	}
}

func TestStatusClass(t *testing.T) {
	cases := map[int]string{
		200: "2xx", 204: "2xx", 301: "3xx",
		404: "4xx", 500: "5xx", 0: "unknown", 999: "unknown",
	}
	for code, want := range cases {
		if got := statusClass(code); got != want {
			t.Errorf("statusClass(%d) = %q, want %q", code, got, want)
		}
	}
}

func keys(m map[string]string) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}
