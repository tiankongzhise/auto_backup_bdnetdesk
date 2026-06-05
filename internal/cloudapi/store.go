package cloudapi

import "context"

type Store interface {
	RegisterDevice(ctx context.Context, device Device, tokenHash string) error
	DeviceByTokenHash(ctx context.Context, tokenHash string) (Device, bool, error)
	ApplyRevisions(ctx context.Context, deviceID string, events []RevisionEvent) ([]RevisionResult, error)
	GetContent(ctx context.Context, contentID string) (ContentObject, bool, error)
	GetArchive(ctx context.Context, archiveSHA256 string) (ArchiveObject, bool, error)
	GetEntitySummary(ctx context.Context, entityID string) (EntitySummary, bool, error)
	Ping(ctx context.Context) error
}
