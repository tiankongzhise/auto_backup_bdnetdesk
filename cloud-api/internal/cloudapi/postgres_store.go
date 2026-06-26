package cloudapi

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgtype"
	"github.com/jackc/pgx/v5/pgxpool"
)

type PostgresStore struct {
	pool *pgxpool.Pool
}

func NewPostgresStore(pool *pgxpool.Pool) *PostgresStore {
	return &PostgresStore{pool: pool}
}

func (s *PostgresStore) RegisterDevice(ctx context.Context, device Device, tokenHash string) error {
	tx, err := s.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return err
	}
	defer func() {
		_ = tx.Rollback(ctx)
	}()

	_, err = tx.Exec(ctx, `
INSERT INTO devices (
    device_id,
    device_token_hash,
    device_fingerprint_hash,
    device_name,
    hostname,
    os_version,
    client_version
) VALUES ($1, $2, $3, $4, $5, $6, $7)
ON CONFLICT (device_id) DO UPDATE SET
    device_token_hash = EXCLUDED.device_token_hash,
    device_fingerprint_hash = COALESCE(NULLIF(devices.device_fingerprint_hash, ''), EXCLUDED.device_fingerprint_hash),
    device_name = EXCLUDED.device_name,
    hostname = EXCLUDED.hostname,
    os_version = EXCLUDED.os_version,
    client_version = EXCLUDED.client_version,
    last_seen_at = now()
`, device.DeviceID, tokenHash, device.DeviceFingerprintHash, device.DeviceName, device.Hostname, device.OSVersion, device.ClientVersion)
	if err != nil {
		return err
	}

	_, err = tx.Exec(ctx, `
INSERT INTO device_tokens (
    device_token_hash,
    device_id
) VALUES ($1, $2)
ON CONFLICT (device_token_hash) DO UPDATE SET
    device_id = EXCLUDED.device_id,
    revoked_at = NULL,
    last_seen_at = now()
`, tokenHash, device.DeviceID)
	if err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func (s *PostgresStore) DeviceByTokenHash(ctx context.Context, tokenHash string) (Device, bool, error) {
	var device Device
	var revoked bool
	err := s.pool.QueryRow(ctx, `
SELECT
    d.device_id,
    d.device_fingerprint_hash,
    d.device_name,
    d.hostname,
    d.os_version,
    d.client_version,
    d.revoked_at IS NOT NULL OR dt.revoked_at IS NOT NULL
FROM device_tokens dt
JOIN devices d ON d.device_id = dt.device_id
WHERE dt.device_token_hash = $1
`, tokenHash).Scan(
		&device.DeviceID,
		&device.DeviceFingerprintHash,
		&device.DeviceName,
		&device.Hostname,
		&device.OSVersion,
		&device.ClientVersion,
		&revoked,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return Device{}, false, nil
	}
	if err != nil {
		return Device{}, false, err
	}

	device.Revoked = revoked
	_, _ = s.pool.Exec(ctx, `UPDATE devices SET last_seen_at = now() WHERE device_id = $1`, device.DeviceID)
	_, _ = s.pool.Exec(ctx, `UPDATE device_tokens SET last_seen_at = now() WHERE device_token_hash = $1`, tokenHash)
	return device, true, nil
}

func (s *PostgresStore) DeviceByID(ctx context.Context, deviceID string) (Device, bool, error) {
	var device Device
	var revoked bool
	err := s.pool.QueryRow(ctx, `
SELECT
    device_id,
    device_fingerprint_hash,
    device_name,
    hostname,
    os_version,
    client_version,
    revoked_at IS NOT NULL
FROM devices
WHERE device_id = $1
`, deviceID).Scan(
		&device.DeviceID,
		&device.DeviceFingerprintHash,
		&device.DeviceName,
		&device.Hostname,
		&device.OSVersion,
		&device.ClientVersion,
		&revoked,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return Device{}, false, nil
	}
	if err != nil {
		return Device{}, false, err
	}
	device.Revoked = revoked
	return device, true, nil
}

func (s *PostgresStore) ApplyRevisions(ctx context.Context, deviceID string, events []RevisionEvent) ([]RevisionResult, error) {
	results := make([]RevisionResult, 0, len(events))
	for _, event := range events {
		result, err := s.applyRevision(ctx, deviceID, event)
		if err != nil {
			return nil, err
		}
		results = append(results, result)
	}
	return results, nil
}

func (s *PostgresStore) applyRevision(ctx context.Context, deviceID string, event RevisionEvent) (RevisionResult, error) {
	result := RevisionResult{
		EventID:    event.EventID,
		EntityID:   event.EntityID,
		RevisionID: event.RevisionID,
	}

	contentIndex, hasContentIndex, err := extractContentIndex(event)
	if err != nil {
		result.Status = StatusRejected
		result.Reason = err.Error()
		return result, nil
	}
	archiveIndex, hasArchiveIndex, err := extractArchiveIndex(event)
	if err != nil {
		result.Status = StatusRejected
		result.Reason = err.Error()
		return result, nil
	}

	tx, err := s.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return RevisionResult{}, err
	}
	defer func() {
		_ = tx.Rollback(ctx)
	}()

	if _, err := tx.Exec(ctx, `SELECT pg_advisory_xact_lock(hashtext($1))`, event.EntityID); err != nil {
		return RevisionResult{}, err
	}

	existingByEvent, ok, err := revisionExistsByEventID(ctx, tx, event.EventID)
	if err != nil {
		return RevisionResult{}, err
	}
	if ok {
		result.EntityID = existingByEvent.EntityID
		result.RevisionID = existingByEvent.RevisionID
		result.Status = StatusDuplicate
		if err := tx.Commit(ctx); err != nil {
			return RevisionResult{}, err
		}
		return result, nil
	}

	existingByRevision, ok, err := revisionExistsByEntityRevision(ctx, tx, event.EntityID, event.RevisionID)
	if err != nil {
		return RevisionResult{}, err
	}
	if ok {
		result.Status = existingByRevision.Status
		if result.Status == StatusSynced {
			result.Status = StatusDuplicate
		}
		if err := tx.Commit(ctx); err != nil {
			return RevisionResult{}, err
		}
		return result, nil
	}

	current, hasCurrent, err := getCloudEntityForUpdate(ctx, tx, event.EntityID)
	if err != nil {
		return RevisionResult{}, err
	}

	if hasCurrent && isConflict(current, event) {
		if err := insertEntityRevision(ctx, tx, deviceID, event, StatusConflict, current.RevisionID); err != nil {
			return RevisionResult{}, err
		}
		if err := tx.Commit(ctx); err != nil {
			return RevisionResult{}, err
		}
		result.Status = StatusConflict
		result.CloudDataVersion = current.DataVersion
		result.CloudRevisionID = current.RevisionID
		return result, nil
	}

	if hasCurrent && current.DataVersion == event.DataVersion && current.CanonicalRecordSHA256 == event.CanonicalRecordSHA256 {
		if err := insertEntityRevision(ctx, tx, deviceID, event, StatusDuplicate, current.RevisionID); err != nil {
			return RevisionResult{}, err
		}
		if err := tx.Commit(ctx); err != nil {
			return RevisionResult{}, err
		}
		result.Status = StatusDuplicate
		result.CloudDataVersion = current.DataVersion
		result.CloudRevisionID = current.RevisionID
		return result, nil
	}

	if err := insertEntityRevision(ctx, tx, deviceID, event, StatusSynced, ""); err != nil {
		return RevisionResult{}, err
	}
	if err := upsertCloudEntity(ctx, tx, deviceID, event); err != nil {
		return RevisionResult{}, err
	}
	if hasContentIndex {
		if err := upsertContentObject(ctx, tx, event.EntityID, contentIndex); err != nil {
			return RevisionResult{}, err
		}
	}
	if hasArchiveIndex {
		if err := upsertArchiveObject(ctx, tx, event.EntityID, archiveIndex); err != nil {
			return RevisionResult{}, err
		}
	}
	if err := tx.Commit(ctx); err != nil {
		return RevisionResult{}, err
	}

	result.Status = StatusSynced
	return result, nil
}

func (s *PostgresStore) GetContent(ctx context.Context, contentID string) (ContentObject, bool, error) {
	var content ContentObject
	err := s.pool.QueryRow(ctx, `
SELECT content_id, file_sha256, size_bytes, latest_entity_id, updated_at
FROM content_objects
WHERE content_id = $1
`, contentID).Scan(
		&content.ContentID,
		&content.FileSHA256,
		&content.SizeBytes,
		&content.LatestEntityID,
		&content.UpdatedAt,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return ContentObject{}, false, nil
	}
	if err != nil {
		return ContentObject{}, false, err
	}
	return content, true, nil
}

func (s *PostgresStore) GetArchive(ctx context.Context, archiveSHA256 string) (ArchiveObject, bool, error) {
	var archive ArchiveObject
	err := s.pool.QueryRow(ctx, `
SELECT archive_sha256, archive_size, remote_path, remote_verified, latest_entity_id, updated_at
FROM archive_objects
WHERE archive_sha256 = $1
`, archiveSHA256).Scan(
		&archive.ArchiveSHA256,
		&archive.ArchiveSize,
		&archive.RemotePath,
		&archive.RemoteVerified,
		&archive.LatestEntityID,
		&archive.UpdatedAt,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return ArchiveObject{}, false, nil
	}
	if err != nil {
		return ArchiveObject{}, false, err
	}
	return archive, true, nil
}

func (s *PostgresStore) GetEntitySummary(ctx context.Context, entityID string) (EntitySummary, bool, error) {
	var summary EntitySummary
	var deletedAt pgtype.Timestamptz
	err := s.pool.QueryRow(ctx, `
SELECT
    entity_id,
    entity_type,
    data_version,
    revision_id,
    canonical_record_sha256,
    updated_by_device_id,
    deleted_at
FROM cloud_entities
WHERE entity_id = $1
`, entityID).Scan(
		&summary.EntityID,
		&summary.EntityType,
		&summary.DataVersion,
		&summary.RevisionID,
		&summary.CanonicalRecordSHA256,
		&summary.UpdatedByDeviceID,
		&deletedAt,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return EntitySummary{}, false, nil
	}
	if err != nil {
		return EntitySummary{}, false, err
	}
	if deletedAt.Valid {
		value := deletedAt.Time
		summary.DeletedAt = &value
	}

	rows, err := s.pool.Query(ctx, `
SELECT event_id, revision_id, data_version, apply_status, canonical_record_sha256, created_at
FROM entity_revisions
WHERE entity_id = $1
ORDER BY data_version DESC, created_at DESC
LIMIT 10
`, entityID)
	if err != nil {
		return EntitySummary{}, false, err
	}
	defer rows.Close()

	for rows.Next() {
		var revision RevisionSummary
		if err := rows.Scan(
			&revision.EventID,
			&revision.RevisionID,
			&revision.DataVersion,
			&revision.ApplyStatus,
			&revision.CanonicalRecordSHA256,
			&revision.CreatedAt,
		); err != nil {
			return EntitySummary{}, false, err
		}
		summary.RecentRevisionSummaries = append(summary.RecentRevisionSummaries, revision)
	}
	if err := rows.Err(); err != nil {
		return EntitySummary{}, false, err
	}

	return summary, true, nil
}

func (s *PostgresStore) ListBackupHistory(ctx context.Context, deviceID string, includeRelatedDevices bool, limit int) ([]BackupHistoryEntity, error) {
	rows, err := s.pool.Query(ctx, `
WITH visible_devices AS (
    SELECT $1::text AS device_id
    UNION
    SELECT b2.device_id
    FROM baidu_account_device_bindings b1
    JOIN baidu_account_device_bindings b2
        ON b2.account_id = b1.account_id
    WHERE $3::boolean
      AND b1.device_id = $1
)
SELECT
    entity_id,
    entity_type,
    data_version,
    revision_id,
    canonical_record_sha256,
    updated_by_device_id,
    payload_json
FROM cloud_entities
WHERE deleted_at IS NULL
  AND entity_type IN (
      'backup_jobs',
      'backup_sources',
      'file_items',
      'folder_items',
      'content_objects',
      'content_references',
      'archives',
      'archive_members',
      'remote_objects'
  )
  AND (
      updated_by_device_id IN (SELECT device_id FROM visible_devices)
      OR payload_json->>'device_id' IN (SELECT device_id FROM visible_devices)
      OR payload_json->>'updated_by_device_id' IN (SELECT device_id FROM visible_devices)
  )
ORDER BY
    CASE entity_type
        WHEN 'backup_jobs' THEN 1
        WHEN 'backup_sources' THEN 2
        WHEN 'file_items' THEN 3
        WHEN 'folder_items' THEN 4
        WHEN 'content_objects' THEN 5
        WHEN 'content_references' THEN 6
        WHEN 'archives' THEN 7
        WHEN 'archive_members' THEN 8
        WHEN 'remote_objects' THEN 9
        ELSE 99
    END,
    updated_at,
    entity_id
LIMIT $2
`, deviceID, limit, includeRelatedDevices)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var entities []BackupHistoryEntity
	for rows.Next() {
		var entity BackupHistoryEntity
		if err := rows.Scan(
			&entity.EntityID,
			&entity.EntityType,
			&entity.DataVersion,
			&entity.RevisionID,
			&entity.CanonicalRecordSHA256,
			&entity.UpdatedByDeviceID,
			&entity.Payload,
		); err != nil {
			return nil, err
		}
		entities = append(entities, entity)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	return entities, nil
}

func (s *PostgresStore) Ping(ctx context.Context) error {
	return s.pool.Ping(ctx)
}

func (s *PostgresStore) CheckSchema(ctx context.Context) (SchemaReadiness, error) {
	required := map[string][]string{
		"devices": {
			"device_id",
			"device_token_hash",
			"device_fingerprint_hash",
			"device_name",
			"hostname",
			"os_version",
			"client_version",
			"revoked_at",
		},
		"device_tokens": {
			"device_token_hash",
			"device_id",
			"revoked_at",
		},
		"cloud_entities": {
			"entity_id",
			"entity_type",
			"schema_version",
			"data_version",
			"revision_id",
			"updated_by_device_id",
			"canonical_record_sha256",
			"payload_json",
		},
		"entity_revisions": {
			"event_id",
			"entity_id",
			"revision_id",
			"apply_status",
			"payload_json",
		},
		"content_objects": {
			"content_id",
			"file_sha256",
			"size_bytes",
			"latest_entity_id",
		},
		"archive_objects": {
			"archive_sha256",
			"archive_size",
			"remote_path",
			"remote_verified",
			"latest_entity_id",
		},
		"baidu_accounts": {
			"account_id",
			"baidu_uid",
			"encrypted_token_json",
			"encryption_method",
			"token_version",
		},
		"baidu_auth_sessions": {
			"session_id",
			"requested_by_device_id",
			"state",
			"device_code",
			"authorization_code",
		},
		"baidu_account_device_bindings": {
			"account_id",
			"device_id",
			"token_expires_at",
			"encryption_method",
			"encrypted_token_json",
			"private_key_hint",
			"token_version",
			"last_verified_at",
			"last_verify_status",
		},
		"baidu_token_refresh_leases": {
			"account_id",
			"lease_id",
			"holder_device_id",
			"expires_at",
		},
		"baidu_device_token_refresh_leases": {
			"account_id",
			"device_id",
			"lease_id",
			"holder_device_id",
			"expires_at",
		},
	}

	rows, err := s.pool.Query(ctx, `
SELECT table_name, column_name
FROM information_schema.columns
WHERE table_schema = current_schema()
`)
	if err != nil {
		return SchemaReadiness{}, err
	}
	defer rows.Close()

	seen := map[string]map[string]bool{}
	for rows.Next() {
		var tableName string
		var columnName string
		if err := rows.Scan(&tableName, &columnName); err != nil {
			return SchemaReadiness{}, err
		}
		if seen[tableName] == nil {
			seen[tableName] = map[string]bool{}
		}
		seen[tableName][columnName] = true
	}
	if err := rows.Err(); err != nil {
		return SchemaReadiness{}, err
	}

	var missingTables []string
	var missingColumns []string
	for tableName, columns := range required {
		tableColumns, ok := seen[tableName]
		if !ok {
			missingTables = append(missingTables, tableName)
			continue
		}
		for _, columnName := range columns {
			if !tableColumns[columnName] {
				missingColumns = append(missingColumns, tableName+"."+columnName)
			}
		}
	}
	sort.Strings(missingTables)
	sort.Strings(missingColumns)

	return SchemaReadiness{
		Ready:          len(missingTables) == 0 && len(missingColumns) == 0,
		MissingTables:  missingTables,
		MissingColumns: missingColumns,
	}, nil
}

func (s *PostgresStore) CreateBaiduAuthSession(ctx context.Context, session BaiduAuthSession) error {
	_, err := s.pool.Exec(ctx, `
INSERT INTO baidu_auth_sessions (
    session_id,
    flow,
    status,
    requested_by_device_id,
    state,
    scope,
    encryption_method,
    rsa_public_key_pem,
    private_key_hint,
    device_code,
    user_code,
    verification_url,
    qrcode_url,
    auth_url,
    expires_at
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15
)
`, session.SessionID,
		session.Flow,
		session.Status,
		session.RequestedByDeviceID,
		session.State,
		session.Scope,
		session.EncryptionMethod,
		session.RSAPublicKeyPEM,
		session.PrivateKeyHint,
		session.DeviceCode,
		session.UserCode,
		session.VerificationURL,
		session.QRCodeURL,
		session.AuthURL,
		session.ExpiresAt,
	)
	return err
}

func (s *PostgresStore) GetBaiduAuthSession(ctx context.Context, sessionID string) (BaiduAuthSession, bool, error) {
	session, ok, err := queryBaiduAuthSession(ctx, s.pool, `WHERE session_id = $1`, sessionID)
	return session, ok, err
}

func (s *PostgresStore) GetBaiduAuthSessionByState(ctx context.Context, state string) (BaiduAuthSession, bool, error) {
	session, ok, err := queryBaiduAuthSession(ctx, s.pool, `WHERE state = $1`, state)
	return session, ok, err
}

func (s *PostgresStore) MarkBaiduAuthSessionCallback(ctx context.Context, state string, code string, errorCode string, errorDescription string) (BaiduAuthSession, bool, error) {
	row := s.pool.QueryRow(ctx, `
UPDATE baidu_auth_sessions
SET
    authorization_code = $2,
    error_code = $3,
    error_description = $4,
    status = CASE
        WHEN $3 <> '' THEN 'failed'
        WHEN $2 <> '' THEN 'authorized'
        ELSE status
    END,
    updated_at = now()
WHERE state = $1
RETURNING
    session_id,
    flow,
    status,
    requested_by_device_id,
    state,
    scope,
    encryption_method,
    rsa_public_key_pem,
    private_key_hint,
    device_code,
    user_code,
    verification_url,
    qrcode_url,
    auth_url,
    authorization_code,
    error_code,
    error_description,
    expires_at,
    completed_at,
    account_id
`, state, code, errorCode, errorDescription)
	return scanBaiduAuthSession(row)
}

func (s *PostgresStore) CompleteBaiduAuthSession(ctx context.Context, session BaiduAuthSession, account BaiduAccount, deviceID string) (BaiduAuthSession, BaiduAccount, error) {
	tx, err := s.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return BaiduAuthSession{}, BaiduAccount{}, err
	}
	defer func() {
		_ = tx.Rollback(ctx)
	}()

	if _, err := tx.Exec(ctx, `SELECT pg_advisory_xact_lock(hashtext($1))`, account.BaiduUID); err != nil {
		return BaiduAuthSession{}, BaiduAccount{}, err
	}

	var existingID string
	err = tx.QueryRow(ctx, `
SELECT account_id
FROM baidu_accounts
WHERE baidu_uid = $1
FOR UPDATE
`, account.BaiduUID).Scan(&existingID)
	if errors.Is(err, pgx.ErrNoRows) {
		_, err = tx.Exec(ctx, `
INSERT INTO baidu_accounts (
    account_id,
    baidu_uid,
    baidu_uk,
    display_name,
    scope,
    token_expires_at,
    encryption_method,
    encrypted_token_json,
    private_key_hint,
    token_version,
    last_verified_at,
    last_verify_status
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11, $12
)
`, account.AccountID,
			account.BaiduUID,
			account.BaiduUK,
			account.DisplayName,
			account.Scope,
			account.TokenExpiresAt,
			account.EncryptionMethod,
			string(account.EncryptedToken),
			account.PrivateKeyHint,
			account.TokenVersion,
			account.LastVerifiedAt,
			account.LastVerifyStatus,
		)
		if err != nil {
			return BaiduAuthSession{}, BaiduAccount{}, err
		}
	} else if err != nil {
		return BaiduAuthSession{}, BaiduAccount{}, err
	} else {
		account.AccountID = existingID
		_, err = tx.Exec(ctx, `
UPDATE baidu_accounts
SET
    baidu_uk = $2,
    display_name = $3,
    scope = $4,
    updated_at = now()
WHERE account_id = $1
`, account.AccountID,
			account.BaiduUK,
			account.DisplayName,
			account.Scope,
		)
		if err != nil {
			return BaiduAuthSession{}, BaiduAccount{}, err
		}
	}

	if err := tx.QueryRow(ctx, `
INSERT INTO baidu_account_device_bindings (
    account_id,
    device_id,
    selected_at,
    token_expires_at,
    encryption_method,
    encrypted_token_json,
    private_key_hint,
    token_version,
    last_verified_at,
    last_verify_status,
    updated_at
) VALUES (
    $1, $2, now(), $3, $4, $5::jsonb, $6, 1, $7, $8, now()
)
ON CONFLICT (account_id, device_id) DO UPDATE SET
    selected_at = now(),
    token_expires_at = EXCLUDED.token_expires_at,
    encryption_method = EXCLUDED.encryption_method,
    encrypted_token_json = EXCLUDED.encrypted_token_json,
    private_key_hint = EXCLUDED.private_key_hint,
    token_version = baidu_account_device_bindings.token_version + 1,
    last_verified_at = EXCLUDED.last_verified_at,
    last_verify_status = EXCLUDED.last_verify_status,
    updated_at = now()
RETURNING token_version
`, account.AccountID,
		deviceID,
		account.TokenExpiresAt,
		account.EncryptionMethod,
		string(account.EncryptedToken),
		account.PrivateKeyHint,
		account.LastVerifiedAt,
		account.LastVerifyStatus,
	).Scan(&account.TokenVersion); err != nil {
		return BaiduAuthSession{}, BaiduAccount{}, err
	}

	var completedAt time.Time
	err = tx.QueryRow(ctx, `
UPDATE baidu_auth_sessions
SET
    status = 'completed',
    completed_at = now(),
    account_id = $2,
    device_code = '',
    authorization_code = '',
    updated_at = now()
WHERE session_id = $1
RETURNING completed_at
`, session.SessionID, account.AccountID).Scan(&completedAt)
	if err != nil {
		return BaiduAuthSession{}, BaiduAccount{}, err
	}

	if err := tx.Commit(ctx); err != nil {
		return BaiduAuthSession{}, BaiduAccount{}, err
	}

	session.Status = BaiduAuthStatusCompleted
	session.CompletedAt = &completedAt
	session.AccountID = account.AccountID
	account.DeviceID = deviceID
	account.Selected = true
	account.CurrentDevice = true
	return session, account, nil
}

func (s *PostgresStore) ListBaiduAccounts(ctx context.Context, deviceID string) ([]BaiduAccount, error) {
	rows, err := s.pool.Query(ctx, `
SELECT
    a.account_id,
    COALESCE(b.device_id, '') AS device_id,
    a.baidu_uid,
    a.baidu_uk,
    a.display_name,
    a.scope,
    COALESCE(b.token_expires_at, 'epoch'::timestamptz) AS token_expires_at,
    COALESCE(b.encryption_method, '') AS encryption_method,
    COALESCE(b.encrypted_token_json, '{}'::jsonb) AS encrypted_token_json,
    COALESCE(b.private_key_hint, '') AS private_key_hint,
    COALESCE(b.token_version, 0) AS token_version,
    b.last_verified_at,
    COALESCE(b.last_verify_status, 'unknown') AS last_verify_status,
    COALESCE(b.device_id = $1, false) AS selected,
    COALESCE(b.device_id = $1, false) AS current_device
FROM baidu_accounts a
LEFT JOIN baidu_account_device_bindings b
    ON b.account_id = a.account_id
ORDER BY current_device DESC, selected DESC, COALESCE(b.updated_at, a.updated_at) DESC
`, deviceID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	accounts := []BaiduAccount{}
	for rows.Next() {
		account, err := scanBaiduAccountFromRows(rows)
		if err != nil {
			return nil, err
		}
		accounts = append(accounts, account)
	}
	return accounts, rows.Err()
}

func (s *PostgresStore) SelectBaiduAccount(ctx context.Context, accountID string, deviceID string) (BaiduAccount, bool, error) {
	tx, err := s.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return BaiduAccount{}, false, err
	}
	defer func() {
		_ = tx.Rollback(ctx)
	}()

	_, ok, err := queryBaiduAccountTx(ctx, tx, accountID, deviceID)
	if err != nil || !ok {
		return BaiduAccount{}, ok, err
	}
	if _, err := tx.Exec(ctx, `
INSERT INTO baidu_account_device_bindings (account_id, device_id, selected_at)
VALUES ($1, $2, now())
ON CONFLICT (account_id, device_id) DO UPDATE SET selected_at = now()
`, accountID, deviceID); err != nil {
		return BaiduAccount{}, false, err
	}
	if err := tx.Commit(ctx); err != nil {
		return BaiduAccount{}, false, err
	}
	selected, ok, err := queryBaiduAccount(ctx, s.pool, accountID, deviceID)
	if err != nil || !ok {
		return BaiduAccount{}, ok, err
	}
	return selected, true, nil
}

func (s *PostgresStore) GetBaiduAccount(ctx context.Context, accountID string, deviceID string) (BaiduAccount, bool, error) {
	account, ok, err := queryBaiduAuthorizedAccount(ctx, s.pool, accountID, deviceID)
	return account, ok, err
}

func (s *PostgresStore) UpdateBaiduAccountToken(ctx context.Context, accountID string, deviceID string, expectedVersion int64, update BaiduAccount) (BaiduAccount, bool, error) {
	row := s.pool.QueryRow(ctx, `
UPDATE baidu_account_device_bindings b
SET
    token_expires_at = $3,
    encryption_method = $4,
    encrypted_token_json = $5::jsonb,
    private_key_hint = $6,
    token_version = token_version + 1,
    last_verified_at = $7,
    last_verify_status = $8,
    updated_at = now()
FROM baidu_accounts a
WHERE b.account_id = $1
  AND b.device_id = $9
  AND b.account_id = a.account_id
  AND b.token_version = $2
  AND b.encrypted_token_json IS NOT NULL
RETURNING
    a.account_id,
    b.device_id,
    a.baidu_uid,
    a.baidu_uk,
    a.display_name,
    a.scope,
    b.token_expires_at,
    b.encryption_method,
    b.encrypted_token_json,
    b.private_key_hint,
    b.token_version,
    b.last_verified_at,
    b.last_verify_status,
    true AS selected,
    true AS current_device
`, accountID,
		expectedVersion,
		update.TokenExpiresAt,
		update.EncryptionMethod,
		string(update.EncryptedToken),
		update.PrivateKeyHint,
		update.LastVerifiedAt,
		update.LastVerifyStatus,
		deviceID,
	)
	account, ok, err := scanBaiduAccount(row)
	if err != nil || ok {
		return account, ok, err
	}
	current, currentOK, currentErr := queryBaiduAuthorizedAccount(ctx, s.pool, accountID, deviceID)
	if currentErr != nil || !currentOK {
		return BaiduAccount{}, false, currentErr
	}
	return current, false, nil
}

func (s *PostgresStore) AcquireBaiduRefreshLease(ctx context.Context, accountID string, deviceID string, leaseID string, durationSeconds int64) (BaiduRefreshLease, bool, error) {
	tx, err := s.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return BaiduRefreshLease{}, false, err
	}
	defer func() {
		_ = tx.Rollback(ctx)
	}()

	if _, err := tx.Exec(ctx, `SELECT pg_advisory_xact_lock(hashtext($1))`, "baidu_lease:"+accountID); err != nil {
		return BaiduRefreshLease{}, false, err
	}
	if _, ok, err := queryBaiduAuthorizedAccountTx(ctx, tx, accountID, deviceID); err != nil || !ok {
		return BaiduRefreshLease{}, false, err
	}

	var existing BaiduRefreshLease
	err = tx.QueryRow(ctx, `
SELECT account_id, lease_id, holder_device_id, expires_at
FROM baidu_device_token_refresh_leases
WHERE account_id = $1
  AND device_id = $2
FOR UPDATE
`, accountID, deviceID).Scan(&existing.AccountID, &existing.LeaseID, &existing.HolderDeviceID, &existing.ExpiresAt)
	if err != nil && !errors.Is(err, pgx.ErrNoRows) {
		return BaiduRefreshLease{}, false, err
	}
	now := time.Now().UTC()
	if err == nil && now.Before(existing.ExpiresAt) && existing.HolderDeviceID != deviceID {
		if err := tx.Commit(ctx); err != nil {
			return BaiduRefreshLease{}, false, err
		}
		return existing, false, nil
	}

	var lease BaiduRefreshLease
	err = tx.QueryRow(ctx, `
INSERT INTO baidu_device_token_refresh_leases (
    account_id,
    device_id,
    lease_id,
    holder_device_id,
    expires_at
) VALUES (
    $1, $2, $3, $4, now() + make_interval(secs => $5)
)
ON CONFLICT (account_id, device_id) DO UPDATE SET
    lease_id = EXCLUDED.lease_id,
    holder_device_id = EXCLUDED.holder_device_id,
    expires_at = EXCLUDED.expires_at,
    updated_at = now()
RETURNING account_id, lease_id, holder_device_id, expires_at
`, accountID, deviceID, leaseID, deviceID, durationSeconds).Scan(&lease.AccountID, &lease.LeaseID, &lease.HolderDeviceID, &lease.ExpiresAt)
	if err != nil {
		return BaiduRefreshLease{}, false, err
	}
	if err := tx.Commit(ctx); err != nil {
		return BaiduRefreshLease{}, false, err
	}
	return lease, true, nil
}

type queryRower interface {
	QueryRow(ctx context.Context, sql string, args ...any) pgx.Row
}

type scanner interface {
	Scan(dest ...any) error
}

func queryBaiduAuthSession(ctx context.Context, queryer queryRower, whereClause string, args ...any) (BaiduAuthSession, bool, error) {
	row := queryer.QueryRow(ctx, `
SELECT
    session_id,
    flow,
    status,
    requested_by_device_id,
    state,
    scope,
    encryption_method,
    rsa_public_key_pem,
    private_key_hint,
    device_code,
    user_code,
    verification_url,
    qrcode_url,
    auth_url,
    authorization_code,
    error_code,
    error_description,
    expires_at,
    completed_at,
    account_id
FROM baidu_auth_sessions
`+whereClause, args...)
	return scanBaiduAuthSession(row)
}

func scanBaiduAuthSession(row scanner) (BaiduAuthSession, bool, error) {
	var session BaiduAuthSession
	var completedAt pgtype.Timestamptz
	var accountID pgtype.Text
	err := row.Scan(
		&session.SessionID,
		&session.Flow,
		&session.Status,
		&session.RequestedByDeviceID,
		&session.State,
		&session.Scope,
		&session.EncryptionMethod,
		&session.RSAPublicKeyPEM,
		&session.PrivateKeyHint,
		&session.DeviceCode,
		&session.UserCode,
		&session.VerificationURL,
		&session.QRCodeURL,
		&session.AuthURL,
		&session.AuthorizationCode,
		&session.ErrorCode,
		&session.ErrorDescription,
		&session.ExpiresAt,
		&completedAt,
		&accountID,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return BaiduAuthSession{}, false, nil
	}
	if err != nil {
		return BaiduAuthSession{}, false, err
	}
	if completedAt.Valid {
		value := completedAt.Time
		session.CompletedAt = &value
	}
	if accountID.Valid {
		session.AccountID = accountID.String
	}
	return session, true, nil
}

func queryBaiduAccount(ctx context.Context, queryer queryRower, accountID string, deviceID string) (BaiduAccount, bool, error) {
	row := queryer.QueryRow(ctx, `
SELECT
    a.account_id,
    COALESCE(b.device_id, '') AS device_id,
    a.baidu_uid,
    a.baidu_uk,
    a.display_name,
    a.scope,
    COALESCE(b.token_expires_at, 'epoch'::timestamptz) AS token_expires_at,
    COALESCE(b.encryption_method, '') AS encryption_method,
    COALESCE(b.encrypted_token_json, '{}'::jsonb) AS encrypted_token_json,
    COALESCE(b.private_key_hint, '') AS private_key_hint,
    COALESCE(b.token_version, 0) AS token_version,
    b.last_verified_at,
    COALESCE(b.last_verify_status, 'unknown') AS last_verify_status,
    COALESCE(b.device_id = $2, false) AS selected,
    COALESCE(b.device_id = $2, false) AS current_device
FROM baidu_accounts a
LEFT JOIN baidu_account_device_bindings b
    ON b.account_id = a.account_id
   AND b.device_id = $2
WHERE a.account_id = $1
`, accountID, deviceID)
	return scanBaiduAccount(row)
}

func queryBaiduAccountTx(ctx context.Context, tx pgx.Tx, accountID string, deviceID string) (BaiduAccount, bool, error) {
	return queryBaiduAccount(ctx, tx, accountID, deviceID)
}

func queryBaiduAuthorizedAccount(ctx context.Context, queryer queryRower, accountID string, deviceID string) (BaiduAccount, bool, error) {
	row := queryer.QueryRow(ctx, `
SELECT
    a.account_id,
    b.device_id,
    a.baidu_uid,
    a.baidu_uk,
    a.display_name,
    a.scope,
    b.token_expires_at,
    b.encryption_method,
    b.encrypted_token_json,
    b.private_key_hint,
    b.token_version,
    b.last_verified_at,
    b.last_verify_status,
    true AS selected,
    b.device_id = $2 AS current_device
FROM baidu_accounts a
JOIN baidu_account_device_bindings b
    ON b.account_id = a.account_id
   AND b.device_id = $2
WHERE a.account_id = $1
  AND b.encrypted_token_json IS NOT NULL
  AND b.token_version > 0
`, accountID, deviceID)
	return scanBaiduAccount(row)
}

func queryBaiduAuthorizedAccountTx(ctx context.Context, tx pgx.Tx, accountID string, deviceID string) (BaiduAccount, bool, error) {
	return queryBaiduAuthorizedAccount(ctx, tx, accountID, deviceID)
}

func scanBaiduAccount(row scanner) (BaiduAccount, bool, error) {
	account, err := scanBaiduAccountValue(row)
	if errors.Is(err, pgx.ErrNoRows) {
		return BaiduAccount{}, false, nil
	}
	if err != nil {
		return BaiduAccount{}, false, err
	}
	return account, true, nil
}

func scanBaiduAccountFromRows(row scanner) (BaiduAccount, error) {
	return scanBaiduAccountValue(row)
}

func scanBaiduAccountValue(row scanner) (BaiduAccount, error) {
	var account BaiduAccount
	var encryptedToken []byte
	var lastVerifiedAt pgtype.Timestamptz
	err := row.Scan(
		&account.AccountID,
		&account.DeviceID,
		&account.BaiduUID,
		&account.BaiduUK,
		&account.DisplayName,
		&account.Scope,
		&account.TokenExpiresAt,
		&account.EncryptionMethod,
		&encryptedToken,
		&account.PrivateKeyHint,
		&account.TokenVersion,
		&lastVerifiedAt,
		&account.LastVerifyStatus,
		&account.Selected,
		&account.CurrentDevice,
	)
	if err != nil {
		return BaiduAccount{}, err
	}
	if len(encryptedToken) > 0 {
		account.EncryptedToken = append(account.EncryptedToken[:0], encryptedToken...)
	}
	if lastVerifiedAt.Valid {
		value := lastVerifiedAt.Time
		account.LastVerifiedAt = &value
	}
	return account, nil
}

type existingRevision struct {
	EntityID   string
	RevisionID string
	Status     string
}

type currentEntity struct {
	EntityID              string
	DataVersion           int64
	RevisionID            string
	CanonicalRecordSHA256 string
}

func revisionExistsByEventID(ctx context.Context, tx pgx.Tx, eventID string) (existingRevision, bool, error) {
	var revision existingRevision
	err := tx.QueryRow(ctx, `
SELECT entity_id, revision_id, apply_status
FROM entity_revisions
WHERE event_id = $1
`, eventID).Scan(&revision.EntityID, &revision.RevisionID, &revision.Status)
	if errors.Is(err, pgx.ErrNoRows) {
		return existingRevision{}, false, nil
	}
	if err != nil {
		return existingRevision{}, false, err
	}
	return revision, true, nil
}

func revisionExistsByEntityRevision(ctx context.Context, tx pgx.Tx, entityID, revisionID string) (existingRevision, bool, error) {
	var revision existingRevision
	err := tx.QueryRow(ctx, `
SELECT entity_id, revision_id, apply_status
FROM entity_revisions
WHERE entity_id = $1 AND revision_id = $2
`, entityID, revisionID).Scan(&revision.EntityID, &revision.RevisionID, &revision.Status)
	if errors.Is(err, pgx.ErrNoRows) {
		return existingRevision{}, false, nil
	}
	if err != nil {
		return existingRevision{}, false, err
	}
	return revision, true, nil
}

func getCloudEntityForUpdate(ctx context.Context, tx pgx.Tx, entityID string) (currentEntity, bool, error) {
	var current currentEntity
	err := tx.QueryRow(ctx, `
SELECT entity_id, data_version, revision_id, canonical_record_sha256
FROM cloud_entities
WHERE entity_id = $1
FOR UPDATE
`, entityID).Scan(
		&current.EntityID,
		&current.DataVersion,
		&current.RevisionID,
		&current.CanonicalRecordSHA256,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return currentEntity{}, false, nil
	}
	if err != nil {
		return currentEntity{}, false, err
	}
	return current, true, nil
}

func isConflict(current currentEntity, event RevisionEvent) bool {
	if current.DataVersion > event.DataVersion {
		return true
	}
	return current.DataVersion == event.DataVersion &&
		current.RevisionID != event.RevisionID &&
		current.CanonicalRecordSHA256 != event.CanonicalRecordSHA256
}

func insertEntityRevision(ctx context.Context, tx pgx.Tx, deviceID string, event RevisionEvent, status string, conflictOfRevisionID string) error {
	var deletedAt any
	if event.DeletedAt != nil {
		deletedAt = *event.DeletedAt
	}
	var conflictOf any
	if conflictOfRevisionID != "" {
		conflictOf = conflictOfRevisionID
	}

	updatedAt := time.Now().UTC()
	if event.UpdatedAt != nil {
		updatedAt = event.UpdatedAt.UTC()
	}

	_, err := tx.Exec(ctx, `
INSERT INTO entity_revisions (
    event_id,
    entity_id,
    entity_type,
    schema_version,
    data_version,
    revision_id,
    operation,
    payload_json,
    canonical_record_sha256,
    updated_at,
    updated_by_device_id,
    deleted_at,
    apply_status,
    conflict_of_revision_id
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11, $12, $13, $14
)
`, event.EventID,
		event.EntityID,
		event.EntityType,
		event.SchemaVersion,
		event.DataVersion,
		event.RevisionID,
		event.Operation,
		string(event.Payload),
		event.CanonicalRecordSHA256,
		updatedAt,
		deviceID,
		deletedAt,
		status,
		conflictOf,
	)
	return err
}

func upsertCloudEntity(ctx context.Context, tx pgx.Tx, deviceID string, event RevisionEvent) error {
	deletedAt := event.DeletedAt
	if event.Operation == "delete" && deletedAt == nil {
		now := time.Now().UTC()
		deletedAt = &now
	}

	updatedAt := time.Now().UTC()
	if event.UpdatedAt != nil {
		updatedAt = event.UpdatedAt.UTC()
	}

	_, err := tx.Exec(ctx, `
INSERT INTO cloud_entities (
    entity_id,
    entity_type,
    schema_version,
    data_version,
    revision_id,
    updated_at,
    updated_by_device_id,
    sync_status,
    deleted_at,
    canonical_record_sha256,
    payload_json
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, 'synced', $8, $9, $10::jsonb
)
ON CONFLICT (entity_id) DO UPDATE SET
    entity_type = EXCLUDED.entity_type,
    schema_version = EXCLUDED.schema_version,
    data_version = EXCLUDED.data_version,
    revision_id = EXCLUDED.revision_id,
    updated_at = EXCLUDED.updated_at,
    updated_by_device_id = EXCLUDED.updated_by_device_id,
    sync_status = 'synced',
    deleted_at = EXCLUDED.deleted_at,
    canonical_record_sha256 = EXCLUDED.canonical_record_sha256,
    payload_json = EXCLUDED.payload_json
`, event.EntityID,
		event.EntityType,
		event.SchemaVersion,
		event.DataVersion,
		event.RevisionID,
		updatedAt,
		deviceID,
		deletedAt,
		event.CanonicalRecordSHA256,
		string(event.Payload),
	)
	return err
}

func upsertContentObject(ctx context.Context, tx pgx.Tx, entityID string, content contentIndex) error {
	_, err := tx.Exec(ctx, `
INSERT INTO content_objects (
    content_id,
    file_sha256,
    size_bytes,
    latest_entity_id,
    updated_at
) VALUES (
    $1, $2, $3, $4, now()
)
ON CONFLICT (content_id) DO UPDATE SET
    latest_entity_id = EXCLUDED.latest_entity_id,
    updated_at = now()
`, content.ContentID, content.FileSHA256, content.SizeBytes, entityID)
	return err
}

func upsertArchiveObject(ctx context.Context, tx pgx.Tx, entityID string, archive archiveIndex) error {
	_, err := tx.Exec(ctx, `
INSERT INTO archive_objects (
    archive_sha256,
    archive_size,
    remote_path,
    remote_verified,
    latest_entity_id,
    updated_at
) VALUES (
    $1, $2, $3, $4, $5, now()
)
ON CONFLICT (archive_sha256) DO UPDATE SET
    archive_size = GREATEST(archive_objects.archive_size, EXCLUDED.archive_size),
    remote_path = COALESCE(NULLIF(EXCLUDED.remote_path, ''), archive_objects.remote_path),
    remote_verified = archive_objects.remote_verified OR EXCLUDED.remote_verified,
    latest_entity_id = EXCLUDED.latest_entity_id,
    updated_at = now()
`, archive.ArchiveSHA256, archive.ArchiveSize, archive.RemotePath, archive.RemoteVerified, entityID)
	return err
}

func (s *PostgresStore) String() string {
	return fmt.Sprintf("PostgresStore{%p}", s.pool)
}
