-- name: GetDeviceByTokenHash :one
SELECT d.device_id, d.device_name, d.hostname, d.os_version, d.client_version, d.revoked_at
FROM device_tokens dt
JOIN devices d ON d.device_id = dt.device_id
WHERE dt.device_token_hash = $1
  AND dt.revoked_at IS NULL;

-- name: InsertDevice :one
INSERT INTO devices (
    device_id,
    device_token_hash,
    device_fingerprint_hash,
    device_name,
    hostname,
    os_version,
    client_version
) VALUES (
    $1, $2, $3, $4, $5, $6, $7
)
RETURNING device_id, device_name, hostname, os_version, client_version, revoked_at;

-- name: GetCloudEntityForUpdate :one
SELECT entity_id, entity_type, data_version, revision_id, canonical_record_sha256
FROM cloud_entities
WHERE entity_id = $1
FOR UPDATE;

-- name: GetRevisionByEntityRevision :one
SELECT event_id, entity_id, revision_id, apply_status
FROM entity_revisions
WHERE entity_id = $1 AND revision_id = $2;

-- name: GetRevisionByEventID :one
SELECT event_id, entity_id, revision_id, apply_status
FROM entity_revisions
WHERE event_id = $1;

-- name: GetContentObject :one
SELECT content_id, file_sha256, size_bytes, latest_entity_id, updated_at
FROM content_objects
WHERE content_id = $1;

-- name: GetArchiveObject :one
SELECT archive_sha256, archive_size, remote_path, remote_verified, latest_entity_id, updated_at
FROM archive_objects
WHERE archive_sha256 = $1;
