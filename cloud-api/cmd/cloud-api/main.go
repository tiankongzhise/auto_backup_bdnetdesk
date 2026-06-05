package main

import (
	"context"
	"errors"
	"flag"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"auto_backup_bdnetdesk/cloud-api/internal/cloudapi"

	"github.com/jackc/pgx/v5/pgxpool"
)

func main() {
	if len(os.Args) > 1 && os.Args[1] == "migrate" {
		runMigrate(os.Args[2:])
		return
	}
	if len(os.Args) > 1 && os.Args[1] == "serve" {
		runServe(os.Args[2:])
		return
	}

	runServe(os.Args[1:])
}

func runServe(args []string) {
	flags := flag.NewFlagSet("serve", flag.ExitOnError)
	envFile := flags.String("env-file", "", "load environment variables from this file before reading cloud-api config")
	_ = flags.Parse(args)

	bootstrapLogger := newLogger("INFO")
	cfg, err := cloudapi.LoadConfig(cloudapi.ConfigOptions{EnvFile: *envFile})
	if err != nil {
		bootstrapLogger.Error("failed to load config", "err", err, "env_file", *envFile)
		os.Exit(1)
	}

	logger := newLogger(cfg.LogLevel)
	logger.Info("cloud api config loaded", configLogAttrs(cfg)...)

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	logger.Info("postgres connection check starting", cfg.Postgres.LogAttrs()...)
	pool, err := pgxpool.New(ctx, cfg.PostgresDSN)
	if err != nil {
		logger.Error("failed to create postgres pool", withErrAttrs(err, cfg.Postgres.LogAttrs())...)
		os.Exit(1)
	}
	defer pool.Close()

	if err := pool.Ping(ctx); err != nil {
		logger.Error("failed to connect postgres", withErrAttrs(err, cfg.Postgres.LogAttrs())...)
		os.Exit(1)
	}
	logger.Info("postgres connection check passed", cfg.Postgres.LogAttrs()...)

	store := cloudapi.NewPostgresStore(pool)
	ensurePostgresSchemaReady(logger, pool, store)

	server := &http.Server{
		Addr:              cfg.Addr,
		Handler:           cloudapi.NewServer(store, logger, cloudapi.WithBaiduOAuthConfig(cfg.BaiduOAuth)),
		ReadHeaderTimeout: 5 * time.Second,
	}

	go func() {
		logger.Info("cloud api listening", cfg.Listen.LogAttrs()...)
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

func ensurePostgresSchemaReady(logger *slog.Logger, pool *pgxpool.Pool, store cloudapi.Store) {
	schemaCtx, schemaCancel := context.WithTimeout(context.Background(), 15*time.Second)
	schema, err := store.CheckSchema(schemaCtx)
	schemaCancel()
	if err == nil && schema.Ready {
		logger.Info("postgres schema check passed")
		return
	}

	if err != nil {
		logger.Warn("postgres schema check failed; running embedded migrations", "err", err)
	} else {
		logger.Warn(
			"postgres schema is not ready; running embedded migrations",
			"missing_tables", schema.MissingTables,
			"missing_columns", schema.MissingColumns,
		)
	}

	migrationCtx, migrationCancel := context.WithTimeout(context.Background(), 60*time.Second)
	result, err := cloudapi.RunEmbeddedPostgresMigrations(migrationCtx, pool)
	migrationCancel()
	if err != nil {
		logger.Error("failed to run embedded postgres migrations during startup", "err", err)
		os.Exit(1)
	}
	logger.Info("embedded postgres migrations finished", "applied", result.Applied, "skipped", result.Skipped)

	verifyCtx, verifyCancel := context.WithTimeout(context.Background(), 15*time.Second)
	schema, err = store.CheckSchema(verifyCtx)
	verifyCancel()
	if err != nil {
		logger.Error("postgres schema check failed after startup migrations", "err", err)
		os.Exit(1)
	}
	if !schema.Ready {
		logger.Error(
			"postgres schema is still not ready after startup migrations",
			"missing_tables", schema.MissingTables,
			"missing_columns", schema.MissingColumns,
		)
		os.Exit(1)
	}
	logger.Info("postgres schema check passed after startup migrations")
}

func runMigrate(args []string) {
	flags := flag.NewFlagSet("migrate", flag.ExitOnError)
	envFile := flags.String("env-file", "", "load environment variables from this file before reading cloud-api config")
	_ = flags.Parse(args)

	bootstrapLogger := newLogger("INFO")
	cfg, err := cloudapi.LoadConfig(cloudapi.ConfigOptions{EnvFile: *envFile})
	if err != nil {
		bootstrapLogger.Error("failed to load config", "err", err, "env_file", *envFile)
		os.Exit(1)
	}

	logger := newLogger(cfg.LogLevel)
	logger.Info("cloud api migrate config loaded", configLogAttrs(cfg)...)

	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()

	pool, err := pgxpool.New(ctx, cfg.PostgresDSN)
	if err != nil {
		logger.Error("failed to create postgres pool", withErrAttrs(err, cfg.Postgres.LogAttrs())...)
		os.Exit(1)
	}
	defer pool.Close()

	if err := pool.Ping(ctx); err != nil {
		logger.Error("failed to connect postgres", withErrAttrs(err, cfg.Postgres.LogAttrs())...)
		os.Exit(1)
	}

	result, err := cloudapi.RunEmbeddedPostgresMigrations(ctx, pool)
	if err != nil {
		logger.Error("failed to run postgres migrations", "err", err)
		os.Exit(1)
	}
	store := cloudapi.NewPostgresStore(pool)
	schema, err := store.CheckSchema(ctx)
	if err != nil {
		logger.Error("postgres schema check failed after migrations", "err", err)
		os.Exit(1)
	}
	if !schema.Ready {
		logger.Error(
			"postgres schema is still not ready after migrations",
			"missing_tables", schema.MissingTables,
			"missing_columns", schema.MissingColumns,
		)
		os.Exit(1)
	}
	logger.Info("postgres migrations finished", "applied", result.Applied, "skipped", result.Skipped)
}

func newLogger(level string) *slog.Logger {
	var slogLevel slog.Level
	switch strings.ToUpper(strings.TrimSpace(level)) {
	case "DEBUG":
		slogLevel = slog.LevelDebug
	case "WARN", "WARNING":
		slogLevel = slog.LevelWarn
	case "ERROR":
		slogLevel = slog.LevelError
	default:
		slogLevel = slog.LevelInfo
	}
	return slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slogLevel}))
}

func configLogAttrs(cfg cloudapi.Config) []any {
	attrs := []any{
		"app_env", cfg.AppEnv,
		"log_level", cfg.LogLevel,
		"public_base_url", cfg.BaiduOAuth.PublicBaseURL,
		"baidu_redirect_uri", cfg.BaiduOAuth.RedirectURI,
		"baidu_app_key_set", cfg.BaiduOAuth.AppKey != "",
		"baidu_app_secret_set", cfg.BaiduOAuth.AppSecret != "",
	}
	attrs = append(attrs, cfg.Listen.LogAttrs()...)
	attrs = append(attrs, cfg.EnvFiles.LogAttrs()...)
	attrs = append(attrs, cfg.Postgres.LogAttrs()...)
	return attrs
}

func withErrAttrs(err error, attrs []any) []any {
	return append([]any{"err", err}, attrs...)
}
