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

	"auto_backup_bdnetdesk/internal/cloudapi"

	"github.com/jackc/pgx/v5/pgxpool"
)

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{}))
	cfg := cloudapi.LoadConfig()

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	pool, err := pgxpool.New(ctx, cfg.PostgresDSN)
	if err != nil {
		logger.Error("failed to create postgres pool", "err", err)
		os.Exit(1)
	}
	defer pool.Close()

	if err := pool.Ping(ctx); err != nil {
		logger.Error("failed to connect postgres", "err", err)
		os.Exit(1)
	}

	store := cloudapi.NewPostgresStore(pool)
	server := &http.Server{
		Addr:              cfg.Addr,
		Handler:           cloudapi.NewServer(store, logger),
		ReadHeaderTimeout: 5 * time.Second,
	}

	go func() {
		logger.Info("cloud api listening", "addr", cfg.Addr)
		if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			logger.Error("cloud api stopped unexpectedly", "err", err)
			os.Exit(1)
		}
	}()

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)
	<-stop

	shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer shutdownCancel()

	if err := server.Shutdown(shutdownCtx); err != nil {
		logger.Error("cloud api shutdown failed", "err", err)
		os.Exit(1)
	}
	logger.Info("cloud api stopped")
}
