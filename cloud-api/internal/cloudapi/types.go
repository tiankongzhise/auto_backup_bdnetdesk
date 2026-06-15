package cloudapi

import (
	"encoding/json"
	"time"
)

const (
	StatusSynced    = "synced"
	StatusDuplicate = "duplicate"
	StatusConflict  = "conflict"
	StatusRejected  = "rejected"
)

type RegisterDeviceRequest struct {
	DeviceName    string `json:"device_name"`
	Hostname      string `json:"hostname"`
	OSVersion     string `json:"os_version"`
	ClientVersion string `json:"client_version"`
}

type RegisterDeviceResponse struct {
	DeviceID    string `json:"device_id"`
	DeviceToken string `json:"device_token"`
}

type Device struct {
	DeviceID      string
	DeviceName    string
	Hostname      string
	OSVersion     string
	ClientVersion string
	Revoked       bool
}

type RevisionEvent struct {
	EventID               string          `json:"event_id"`
	EntityType            string          `json:"entity_type"`
	EntityID              string          `json:"entity_id"`
	RevisionID            string          `json:"revision_id"`
	SchemaVersion         int             `json:"schema_version"`
	DataVersion           int64           `json:"data_version"`
	Operation             string          `json:"operation"`
	CanonicalRecordSHA256 string          `json:"canonical_record_sha256"`
	Payload               json.RawMessage `json:"payload"`
	UpdatedAt             *time.Time      `json:"updated_at,omitempty"`
	DeletedAt             *time.Time      `json:"deleted_at,omitempty"`
}

type SyncRevisionsRequest struct {
	Events []RevisionEvent `json:"events"`
}

type SyncRevisionsResponse struct {
	Results []RevisionResult `json:"results"`
}

type RevisionResult struct {
	EventID          string `json:"event_id"`
	EntityID         string `json:"entity_id"`
	RevisionID       string `json:"revision_id"`
	Status           string `json:"status"`
	Reason           string `json:"reason,omitempty"`
	CloudDataVersion int64  `json:"cloud_data_version,omitempty"`
	CloudRevisionID  string `json:"cloud_revision_id,omitempty"`
}

type ContentObject struct {
	ContentID      string    `json:"content_id"`
	FileSHA256     string    `json:"file_sha256"`
	SizeBytes      int64     `json:"size_bytes"`
	LatestEntityID string    `json:"latest_entity_id"`
	UpdatedAt      time.Time `json:"updated_at"`
}

type ArchiveObject struct {
	ArchiveSHA256  string    `json:"archive_sha256"`
	ArchiveSize    int64     `json:"archive_size"`
	RemotePath     string    `json:"remote_path"`
	RemoteVerified bool      `json:"remote_verified"`
	LatestEntityID string    `json:"latest_entity_id"`
	UpdatedAt      time.Time `json:"updated_at"`
}

type EntitySummary struct {
	EntityID                string            `json:"entity_id"`
	EntityType              string            `json:"entity_type"`
	DataVersion             int64             `json:"data_version"`
	RevisionID              string            `json:"revision_id"`
	CanonicalRecordSHA256   string            `json:"canonical_record_sha256"`
	UpdatedByDeviceID       string            `json:"updated_by_device_id"`
	DeletedAt               *time.Time        `json:"deleted_at,omitempty"`
	RecentRevisionSummaries []RevisionSummary `json:"recent_revisions"`
}

type RevisionSummary struct {
	EventID               string    `json:"event_id"`
	RevisionID            string    `json:"revision_id"`
	DataVersion           int64     `json:"data_version"`
	ApplyStatus           string    `json:"apply_status"`
	CanonicalRecordSHA256 string    `json:"canonical_record_sha256"`
	CreatedAt             time.Time `json:"created_at"`
}

type BackupHistoryEntity struct {
	EntityID              string          `json:"entity_id"`
	EntityType            string          `json:"entity_type"`
	DataVersion           int64           `json:"data_version"`
	RevisionID            string          `json:"revision_id"`
	CanonicalRecordSHA256 string          `json:"canonical_record_sha256"`
	UpdatedByDeviceID     string          `json:"updated_by_device_id"`
	Payload               json.RawMessage `json:"payload"`
}

type BackupHistoryResponse struct {
	DeviceID string                `json:"device_id"`
	Entities []BackupHistoryEntity `json:"entities"`
}
