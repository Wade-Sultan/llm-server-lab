package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/palladium/commerce/internal/config"
	"github.com/palladium/commerce/internal/server"
)

const (
	// How long to keep serving after SIGTERM before starting the shutdown,
	// covering the window where the pod is still in the Service's endpoints.
	drainDelay = 5 * time.Second

	// Ceiling on finishing in-flight requests. drainDelay + shutdownTimeout
	// must stay under the manifest's terminationGracePeriodSeconds (40), or
	// the kubelet SIGKILLs mid-shutdown.
	shutdownTimeout = 20 * time.Second
)

func main() {
	// Structured JSON logs so Cloud Run's logging picks up fields, not just text.
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	slog.SetDefault(logger)

	cfg, err := config.Load()
	if err != nil {
		slog.Error("failed to load config", "err", err)
		os.Exit(1)
	}

	startCtx, cancelStart := context.WithTimeout(context.Background(), 30*time.Second)
	handler, cleanup, err := server.New(startCtx, cfg, logger)
	cancelStart()
	if err != nil {
		slog.Error("failed to init server", "err", err)
		os.Exit(1)
	}
	defer func() {
		if err := cleanup(); err != nil {
			slog.Error("cleanup failed", "err", err)
		}
	}()

	srv := &http.Server{
		Addr:         ":" + cfg.Port,
		Handler:      handler,
		ReadTimeout:  10 * time.Second,
		WriteTimeout: 15 * time.Second,
		IdleTimeout:  60 * time.Second,
	}

	// Metrics listen on their own port, scraped by GMP directly off the pod IP.
	// See config.MetricsPort for why this is not folded into the API port.
	metricsMux := http.NewServeMux()
	metricsMux.Handle("GET /metrics", server.MetricsHandler())
	metricsSrv := &http.Server{
		Addr:    ":" + cfg.MetricsPort,
		Handler: metricsMux,
		// No WriteTimeout: a large registry served over a slow scrape
		// connection would be cut off mid-body, and a truncated exposition
		// response is a parse error on the GMP side rather than a clean miss.
		ReadTimeout: 10 * time.Second,
		IdleTimeout: 60 * time.Second,
	}

	// Deliberately not reported on serverErr: losing metrics must not take the
	// service down. A failure here is logged and the API keeps serving.
	go func() {
		slog.Info("metrics listening", "addr", metricsSrv.Addr)
		if err := metricsSrv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			slog.Error("metrics server stopped; metrics are no longer exported", "err", err)
		}
	}()

	// Run the listener in its own goroutine so main can block on signals.
	serverErr := make(chan error, 1)
	go func() {
		slog.Info("commerce listening", "addr", srv.Addr, "env", cfg.Env)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			serverErr <- err
		}
	}()

	// Cloud Run sends SIGTERM before reclaiming an instance, as does the
	// kubelet before deleting a pod; handle it cleanly so in-flight requests
	// get a chance to finish.
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)

	select {
	case err := <-serverErr:
		slog.Error("server error", "err", err)
		os.Exit(1)
	case sig := <-stop:
		slog.Info("shutdown signal received", "signal", sig.String())
	}

	// Endpoint removal and SIGTERM delivery race each other, so keep serving
	// briefly after the signal or every rollout drops requests that were
	// already routed here. This is the manifest's preStop sleep done in
	// process: the distroless base image has no shell and no sleep binary,
	// so an exec hook would just log FailedPreStopHook and change nothing.
	slog.Info("draining before shutdown", "drain", drainDelay.String())
	time.Sleep(drainDelay)

	ctx, cancel := context.WithTimeout(context.Background(), shutdownTimeout)
	defer cancel()

	// Metrics go down only after the API has drained, so a scrape landing
	// during the drain window still captures the final counter values for
	// requests served on the way out.
	defer func() {
		if err := metricsSrv.Shutdown(ctx); err != nil {
			slog.Error("metrics shutdown failed", "err", err)
		}
	}()

	if err := srv.Shutdown(ctx); err != nil {
		slog.Error("graceful shutdown failed", "err", err)
		os.Exit(1)
	}
	slog.Info("shutdown complete")
}
