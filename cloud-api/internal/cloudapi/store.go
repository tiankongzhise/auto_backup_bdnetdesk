package cloudapi

import "context"

type SchemaReadiness struct {
	Ready          bool     `json:"ready"`
	MissingTables  []string `json:"missing_tables,omitempty"`
	MissingColumns []string `json:"missing_columns,omitempty"`
}

type Store interface {
	RegisterDevice(ctx context.Context, device Device, tokenHash string) error
	DeviceByTokenHash(ctx context.Context, tokenHash string) (Device, bool, error)
	DeviceByID(ctx context.Context, deviceID string) (Device, bool, error)
	ApplyRevisions(ctx context.Context, deviceID string, events []RevisionEvent) ([]RevisionResult, error)
	GetContent(ctx context.Context, contentID string) (ContentObject, bool, error)
	GetArchive(ctx context.Context, archiveSHA256 string) (ArchiveObject, bool, error)
	GetEntitySummary(ctx context.Context, entityID string) (EntitySummary, bool, error)
	ListBackupHistory(ctx context.Context, deviceID string, includeRelatedDevices bool, limit int) ([]BackupHistoryEntity, error)
	CreateBaiduAuthSession(ctx context.Context, session BaiduAuthSession) error
	GetBaiduAuthSession(ctx context.Context, sessionID string) (BaiduAuthSession, bool, error)
	GetBaiduAuthSessionByState(ctx context.Context, state string) (BaiduAuthSession, bool, error)
	MarkBaiduAuthSessionCallback(ctx context.Context, state string, code string, errorCode string, errorDescription string) (BaiduAuthSession, bool, error)
	CompleteBaiduAuthSession(ctx context.Context, session BaiduAuthSession, account BaiduAccount, deviceID string) (BaiduAuthSession, BaiduAccount, error)
	ListBaiduAccounts(ctx context.Context, deviceID string) ([]BaiduAccount, error)
	SelectBaiduAccount(ctx context.Context, accountID string, deviceID string) (BaiduAccount, bool, error)
	GetBaiduAccount(ctx context.Context, accountID string, deviceID string) (BaiduAccount, bool, error)
	UpdateBaiduAccountToken(ctx context.Context, accountID string, deviceID string, expectedVersion int64, update BaiduAccount) (BaiduAccount, bool, error)
	AcquireBaiduRefreshLease(ctx context.Context, accountID string, deviceID string, leaseID string, durationSeconds int64) (BaiduRefreshLease, bool, error)
	Ping(ctx context.Context) error
	CheckSchema(ctx context.Context) (SchemaReadiness, error)
}
