package cloudapi

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

func TestLoadConfigFromExplicitEnvFile(t *testing.T) {
	envFile := writeTestEnvFile(t, `
APP_ENV=production
LOG_LEVEL=DEBUG
CLOUD_API_ADDR=9321
PUBLIC_BASE_URL=https://backup.example.com
POSTGRES_HOST=10.0.0.8
POSTGRES_PORT=6543
POSTGRES_DB=backup_prod
POSTGRES_USER=backup_user
POSTGRES_PASSWORD=secret
POSTGRES_SSLMODE=require
BAIDU_APP_KEY=baidu-key
BAIDU_APP_SECRET=baidu-secret
`)

	clearConfigEnv(t)
	cfg, err := LoadConfig(ConfigOptions{EnvFile: envFile})
	if err != nil {
		t.Fatalf("LoadConfig: %v", err)
	}

	if cfg.AppEnv != "production" {
		t.Fatalf("expected production app env, got %q", cfg.AppEnv)
	}
	if cfg.LogLevel != "DEBUG" {
		t.Fatalf("expected DEBUG log level, got %q", cfg.LogLevel)
	}
	if cfg.Addr != ":9321" || cfg.Listen.Raw != "9321" || !cfg.Listen.BarePortNormalized {
		t.Fatalf("expected bare port normalization, got %#v", cfg.Listen)
	}
	if cfg.EnvFiles.Mode != "flag" || len(cfg.EnvFiles.Loaded) != 1 {
		t.Fatalf("expected explicit env file to be loaded, got %#v", cfg.EnvFiles)
	}
	if !slices.Contains(cfg.EnvFiles.Loaded[0].SetKeys, "POSTGRES_USER") {
		t.Fatalf("expected POSTGRES_USER to be loaded, got %#v", cfg.EnvFiles.Loaded[0].SetKeys)
	}
	if cfg.Postgres.Source != "POSTGRES_*" {
		t.Fatalf("expected split postgres config, got %q", cfg.Postgres.Source)
	}
	if cfg.Postgres.Host != "10.0.0.8" ||
		cfg.Postgres.Port != "6543" ||
		cfg.Postgres.Database != "backup_prod" ||
		cfg.Postgres.User != "backup_user" ||
		cfg.Postgres.SSLMode != "require" ||
		!cfg.Postgres.PasswordSet {
		t.Fatalf("unexpected postgres summary: %#v", cfg.Postgres)
	}
	if cfg.PostgresDSN == "" || !strings.Contains(cfg.PostgresDSN, "backup_user") || strings.Contains(cfg.PostgresDSN, "auto_backup_user") {
		t.Fatalf("unexpected postgres dsn: %q", cfg.PostgresDSN)
	}
	if cfg.BaiduOAuth.PublicBaseURL != "https://backup.example.com" ||
		cfg.BaiduOAuth.AppKey != "baidu-key" ||
		cfg.BaiduOAuth.AppSecret != "baidu-secret" {
		t.Fatalf("unexpected baidu config: %#v", cfg.BaiduOAuth)
	}
}

func TestLoadConfigDoesNotOverrideProcessEnvironment(t *testing.T) {
	envFile := writeTestEnvFile(t, `
POSTGRES_HOST=from-file-host
POSTGRES_USER=from_file_user
POSTGRES_PASSWORD=from-file-password
`)

	clearConfigEnv(t)
	t.Setenv("POSTGRES_HOST", "from-env-host")
	t.Setenv("POSTGRES_USER", "from_env_user")
	cfg, err := LoadConfig(ConfigOptions{EnvFile: envFile})
	if err != nil {
		t.Fatalf("LoadConfig: %v", err)
	}

	if cfg.Postgres.Host != "from-env-host" || cfg.Postgres.User != "from_env_user" {
		t.Fatalf("expected process environment to win, got %#v", cfg.Postgres)
	}
	if !slices.Contains(cfg.EnvFiles.Loaded[0].PreservedKeys, "POSTGRES_HOST") ||
		!slices.Contains(cfg.EnvFiles.Loaded[0].PreservedKeys, "POSTGRES_USER") {
		t.Fatalf("expected preserved env keys, got %#v", cfg.EnvFiles.Loaded[0].PreservedKeys)
	}
	if !slices.Contains(cfg.EnvFiles.Loaded[0].SetKeys, "POSTGRES_PASSWORD") {
		t.Fatalf("expected missing password to be loaded, got %#v", cfg.EnvFiles.Loaded[0].SetKeys)
	}
}

func TestPostgresDSNTakesPriority(t *testing.T) {
	envFile := writeTestEnvFile(t, `
POSTGRES_DSN=postgres://dsn_user:dsn_password@db.example.com:5439/dsn_db?sslmode=require
POSTGRES_HOST=ignored-host
POSTGRES_USER=ignored-user
POSTGRES_PASSWORD=ignored-password
`)

	clearConfigEnv(t)
	cfg, err := LoadConfig(ConfigOptions{EnvFile: envFile})
	if err != nil {
		t.Fatalf("LoadConfig: %v", err)
	}

	if cfg.Postgres.Source != "POSTGRES_DSN" {
		t.Fatalf("expected POSTGRES_DSN source, got %q", cfg.Postgres.Source)
	}
	if cfg.Postgres.Host != "db.example.com" ||
		cfg.Postgres.Port != "5439" ||
		cfg.Postgres.Database != "dsn_db" ||
		cfg.Postgres.User != "dsn_user" ||
		cfg.Postgres.SSLMode != "require" ||
		!cfg.Postgres.PasswordSet {
		t.Fatalf("unexpected postgres summary: %#v", cfg.Postgres)
	}
	if cfg.PostgresDSN != "postgres://dsn_user:dsn_password@db.example.com:5439/dsn_db?sslmode=require" {
		t.Fatalf("unexpected dsn: %q", cfg.PostgresDSN)
	}
}

func TestExplicitEnvFileMustExist(t *testing.T) {
	clearConfigEnv(t)
	_, err := LoadConfig(ConfigOptions{EnvFile: filepath.Join(t.TempDir(), "missing.env")})
	if err == nil {
		t.Fatal("expected missing explicit env file error")
	}
}

func writeTestEnvFile(t *testing.T, content string) string {
	t.Helper()

	path := filepath.Join(t.TempDir(), "cloud-api.env")
	if err := os.WriteFile(path, []byte(strings.TrimSpace(content)+"\n"), 0o600); err != nil {
		t.Fatalf("write env file: %v", err)
	}
	return path
}

func clearConfigEnv(t *testing.T) {
	t.Helper()

	for _, key := range []string{
		"APP_ENV",
		"LOG_LEVEL",
		"CLOUD_API_ADDR",
		EnvFileVariable,
		"PUBLIC_BASE_URL",
		"POSTGRES_DSN",
		"POSTGRES_HOST",
		"POSTGRES_PORT",
		"POSTGRES_DB",
		"POSTGRES_USER",
		"POSTGRES_PASSWORD",
		"POSTGRES_SSLMODE",
		"BAIDU_APP_KEY",
		"BAIDU_APP_SECRET",
		"BAIDU_SCOPE",
		"BAIDU_REDIRECT_URI",
		"BAIDU_AUTHORIZE_URL",
		"BAIDU_DEVICE_CODE_URL",
		"BAIDU_TOKEN_URL",
		"BAIDU_USERINFO_URL",
		"BAIDU_DEVICE_VERIFICATION_URL",
	} {
		t.Setenv(key, "")
	}
}
