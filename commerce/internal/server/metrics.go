package server

import (
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

// Metric names and label sets deliberately mirror what
// prometheus-fastapi-instrumentator emits on the builder side
// (backend/app/core/metrics.py), so one Grafana panel can graph both services
// without a per-service query. That is also why `status` is a grouped class
// ("2xx") rather than the exact code: the Python instrumentator groups by
// default, and a panel summing across services needs the label values to mean
// the same thing on both.
var (
	httpRequestsTotal = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "http_requests_total",
			Help: "Total HTTP requests by method, route template and status class.",
		},
		[]string{"method", "handler", "status"},
	)

	httpRequestDuration = promauto.NewHistogramVec(
		prometheus.HistogramOpts{
			Name:    "http_request_duration_seconds",
			Help:    "HTTP request latency by method and route template.",
			Buckets: prometheus.DefBuckets,
		},
		[]string{"method", "handler"},
	)
)

// MetricsHandler serves the default registry, which promauto registers into.
// That default also carries the Go runtime and process collectors — goroutine
// count, GC pauses, heap and open FDs — at no extra wiring cost.
func MetricsHandler() http.Handler {
	return promhttp.Handler()
}

// requestMetrics records request counts and latency, labelled by *route
// template* rather than resolved path.
//
// It takes the mux, which is the whole subtlety here. Go 1.22+ records the
// matched pattern on the request, but the mux sets it on a clone it passes
// down, so a middleware wrapping the mux from the outside never sees it —
// r.Pattern is empty at this level, both before and after next.ServeHTTP.
// Reading r.URL.Path instead would mint one time series per listing UUID and
// blow up cardinality in GMP. mux.Handler(r) re-resolves the pattern without
// serving, which is a map lookup, and yields the bounded label set we want.
func requestMetrics(mux *http.ServeMux) middleware {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			// Unmatched requests (404s, and CORS preflights, which carry a
			// method no route pattern declares) resolve to an empty pattern.
			// Bucketing them under one "other" label keeps a scanner probing
			// random paths from creating a series per path.
			_, pattern := mux.Handler(r)
			// mux.Handler returns the *whole* registered pattern, which for a
			// Go 1.22+ method pattern includes the verb: "GET /api/v1/listings/{id}".
			// Strip it — method is already its own label, and leaving it in
			// would both duplicate that and stop these series lining up with
			// the builder's, whose handler label is the bare path.
			if i := strings.LastIndex(pattern, " "); i >= 0 {
				pattern = pattern[i+1:]
			}
			if pattern == "" {
				pattern = "other"
			}

			start := time.Now()
			rec := &statusRecorder{ResponseWriter: w, status: http.StatusOK}

			next.ServeHTTP(rec, r)

			httpRequestDuration.WithLabelValues(r.Method, pattern).
				Observe(time.Since(start).Seconds())
			httpRequestsTotal.WithLabelValues(r.Method, pattern, statusClass(rec.status)).
				Inc()
		})
	}
}

// statusClass reduces a status code to its class: 200 and 204 both become
// "2xx". Keeps the label bounded to five values and matches the builder's
// grouping.
func statusClass(code int) string {
	if code < 100 || code > 599 {
		return "unknown"
	}
	return strconv.Itoa(code/100) + "xx"
}
