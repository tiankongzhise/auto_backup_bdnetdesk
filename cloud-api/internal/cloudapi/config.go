package cloudapi

import (
	"fmt"
	"net/url"
	"os"
)

type Config struct {
	Addr        string
	PostgresDSN string
}

func LoadConfig() Config {
	addr := getenv("CLOUD_API_ADDR", ":8080")
	dsn := os.Getenv("POSTGRES_DSN")
	if dsn == "" {
		user := getenv("POSTGRES_USER", "auto_backup_user")
		password := os.Getenv("POSTGRES_PASSWORD")
		host := getenv("POSTGRES_HOST", "127.0.0.1")
		port := getenv("POSTGRES_PORT", "5432")
		db := getenv("POSTGRES_DB", "auto_backup_bdnetdesk")
		sslmode := getenv("POSTGRES_SSLMODE", "disable")
		dsn = fmt.Sprintf(
			"postgres://%s:%s@%s:%s/%s?sslmode=%s",
			url.QueryEscape(user),
			url.QueryEscape(password),
			host,
			port,
			url.PathEscape(db),
			url.QueryEscape(sslmode),
		)
	}

	return Config{
		Addr:        addr,
		PostgresDSN: dsn,
	}
}

func getenv(key, fallback string) string {
	value := os.Getenv(key)
	if value == "" {
		return fallback
	}
	return value
}
