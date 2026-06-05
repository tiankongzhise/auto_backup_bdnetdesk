package cloudapi

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"strings"

	cloudapimigrations "auto_backup_bdnetdesk/cloud-api/migrations"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

type MigrationResult struct {
	Applied []string
	Skipped []string
}

func RunEmbeddedPostgresMigrations(ctx context.Context, pool *pgxpool.Pool) (MigrationResult, error) {
	migrations, err := cloudapimigrations.PostgresMigrations()
	if err != nil {
		return MigrationResult{}, err
	}

	tx, err := pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return MigrationResult{}, err
	}
	defer func() {
		_ = tx.Rollback(ctx)
	}()

	if _, err := tx.Exec(ctx, `SELECT pg_advisory_xact_lock(20260605, 9321)`); err != nil {
		return MigrationResult{}, err
	}

	if _, err := tx.Exec(ctx, `
CREATE TABLE IF NOT EXISTS schema_migrations (
    name TEXT PRIMARY KEY,
    checksum_sha256 CHAR(64) NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)`); err != nil {
		return MigrationResult{}, err
	}

	result := MigrationResult{}
	for _, migration := range migrations {
		checksum := sha256Hex(migration.SQL)
		var existingChecksum string
		err := tx.QueryRow(ctx, `
SELECT checksum_sha256
FROM schema_migrations
WHERE name = $1
`, migration.Name).Scan(&existingChecksum)
		if err == nil {
			if existingChecksum != checksum {
				return MigrationResult{}, fmt.Errorf("migration %s checksum changed", migration.Name)
			}
			result.Skipped = append(result.Skipped, migration.Name)
			continue
		}
		if !errors.Is(err, pgx.ErrNoRows) {
			return MigrationResult{}, err
		}

		for _, statement := range splitSQLStatements(migration.SQL) {
			if _, err := tx.Exec(ctx, statement); err != nil {
				return MigrationResult{}, fmt.Errorf("apply migration %s: %w", migration.Name, err)
			}
		}
		if _, err := tx.Exec(ctx, `
INSERT INTO schema_migrations (name, checksum_sha256)
VALUES ($1, $2)
`, migration.Name, checksum); err != nil {
			return MigrationResult{}, err
		}
		result.Applied = append(result.Applied, migration.Name)
	}

	if err := tx.Commit(ctx); err != nil {
		return MigrationResult{}, err
	}
	return result, nil
}

func sha256Hex(value string) string {
	sum := sha256.Sum256([]byte(value))
	return hex.EncodeToString(sum[:])
}

func splitSQLStatements(sql string) []string {
	parts := strings.Split(sql, ";")
	statements := make([]string, 0, len(parts))
	for _, part := range parts {
		statement := strings.TrimSpace(part)
		if statement == "" {
			continue
		}
		statements = append(statements, statement)
	}
	return statements
}
