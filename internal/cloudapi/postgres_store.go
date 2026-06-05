package cloudapi

import (
	"context"
	"errors"
	"fmt"
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
	_, err := s.pool.Exec(ctx, `
INSERT INTO devices (
    device_id,
    device_token_hash,
    device_name,
    hostname,
    os_version,
    client_version
) VALUES ($1, $2, $3, $4, $5, $6)
`, device.DeviceID, tokenHash, device.DeviceName, device.Hostname, device.OSVersion, device.ClientVersion)
	return err
}

func (s *PostgresStore) DeviceByTokenHash(ctx context.Context, tokenHash string) (Device, bool, error) {
	var device Device
	var revoked bool
	err := s.pool.QueryRow(ctx, `
SELECT
    device_id,
    device_name,
    hostname,
    os_version,
    client_version,
    revoked_at IS NOT NULL
FROM devices
WHERE device_token_hash = $1
`, tokenHash).Scan(
		&device.DeviceID,
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

func (s *PostgresStore) Ping(ctx context.Context) error {
	return s.pool.Ping(ctx)
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
