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

	// Run the listener in its own goroutine so main can block on signals.
	serverErr := make(chan error, 1)
	go func() {
		slog.Info("commerce listening", "addr", srv.Addr, "env", cfg.Env)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			serverErr <- err
		}
	}()

	// Cloud Run sends SIGTERM before reclaiming an instance; handle it cleanly
	// so in-flight requests get a chance to finish.
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)

	select {
	case err := <-serverErr:
		slog.Error("server error", "err", err)
		os.Exit(1)
	case sig := <-stop:
		slog.Info("shutdown signal received", "signal", sig.String())
	}

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := srv.Shutdown(ctx); err != nil {
		slog.Error("graceful shutdown failed", "err", err)
		os.Exit(1)
	}
	slog.Info("shutdown complete")
}
