package cloudapi

import (
	"context"
	"encoding/json"
	"sync"
	"time"
)

type memoryStore struct {
	mu                  sync.Mutex
	devices             map[string]Device
	tokenHashes         map[string]string
	entities            map[string]memoryEntity
	revisions           map[string]memoryRevision
	eventIDs            map[string]string
	contents            map[string]ContentObject
	archives            map[string]ArchiveObject
	baiduSessions       map[string]BaiduAuthSession
	baiduSessionStates  map[string]string
	baiduAccounts       map[string]BaiduAccount
	baiduAccountByUID   map[string]string
	baiduAuthorizations map[string]map[string]BaiduAccount
	baiduBindings       map[string]map[string]bool
	baiduLeases         map[string]BaiduRefreshLease
	err                 error
}

type memoryEntity struct {
	EntityID              string
	EntityType            string
	DataVersion           int64
	RevisionID            string
	CanonicalRecordSHA256 string
	UpdatedByDeviceID     string
	Payload               json.RawMessage
	DeletedAt             *time.Time
}

type memoryRevision struct {
	EventID               string
	EntityID              string
	RevisionID            string
	DataVersion           int64
	Status                string
	CanonicalRecordSHA256 string
	CreatedAt             time.Time
}

func newMemoryStore() *memoryStore {
	return &memoryStore{
		devices:             map[string]Device{},
		tokenHashes:         map[string]string{},
		entities:            map[string]memoryEntity{},
		revisions:           map[string]memoryRevision{},
		eventIDs:            map[string]string{},
		contents:            map[string]ContentObject{},
		archives:            map[string]ArchiveObject{},
		baiduSessions:       map[string]BaiduAuthSession{},
		baiduSessionStates:  map[string]string{},
		baiduAccounts:       map[string]BaiduAccount{},
		baiduAccountByUID:   map[string]string{},
		baiduAuthorizations: map[string]map[string]BaiduAccount{},
		baiduBindings:       map[string]map[string]bool{},
		baiduLeases:         map[string]BaiduRefreshLease{},
	}
}

func (s *memoryStore) RegisterDevice(_ context.Context, device Device, tokenHash string) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.err != nil {
		return s.err
	}
	s.devices[device.DeviceID] = device
	s.tokenHashes[tokenHash] = device.DeviceID
	return nil
}

func (s *memoryStore) DeviceByTokenHash(_ context.Context, tokenHash string) (Device, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.err != nil {
		return Device{}, false, s.err
	}
	deviceID, ok := s.tokenHashes[tokenHash]
	if !ok {
		return Device{}, false, nil
	}
	device, ok := s.devices[deviceID]
	return device, ok, nil
}

func (s *memoryStore) ApplyRevisions(_ context.Context, deviceID string, events []RevisionEvent) ([]RevisionResult, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.err != nil {
		return nil, s.err
	}

	results := make([]RevisionResult, 0, len(events))
	for _, event := range events {
		result := s.applyRevisionLocked(deviceID, event)
		results = append(results, result)
	}
	return results, nil
}

func (s *memoryStore) applyRevisionLocked(deviceID string, event RevisionEvent) RevisionResult {
	result := RevisionResult{
		EventID:    event.EventID,
		EntityID:   event.EntityID,
		RevisionID: event.RevisionID,
	}

	contentIndex, hasContentIndex, err := extractContentIndex(event)
	if err != nil {
		result.Status = StatusRejected
		result.Reason = err.Error()
		return result
	}
	archiveIndex, hasArchiveIndex, err := extractArchiveIndex(event)
	if err != nil {
		result.Status = StatusRejected
		result.Reason = err.Error()
		return result
	}

	if existingKey, ok := s.eventIDs[event.EventID]; ok {
		existing := s.revisions[existingKey]
		result.EntityID = existing.EntityID
		result.RevisionID = existing.RevisionID
		result.Status = StatusDuplicate
		return result
	}

	revisionKey := event.EntityID + "\x00" + event.RevisionID
	if existing, ok := s.revisions[revisionKey]; ok {
		result.Status = existing.Status
		if result.Status == StatusSynced {
			result.Status = StatusDuplicate
		}
		return result
	}

	current, hasCurrent := s.entities[event.EntityID]
	if hasCurrent && (current.DataVersion > event.DataVersion ||
		(current.DataVersion == event.DataVersion &&
			current.RevisionID != event.RevisionID &&
			current.CanonicalRecordSHA256 != event.CanonicalRecordSHA256)) {
		s.storeRevisionLocked(event, revisionKey, StatusConflict)
		result.Status = StatusConflict
		result.CloudDataVersion = current.DataVersion
		result.CloudRevisionID = current.RevisionID
		return result
	}

	if hasCurrent &&
		current.DataVersion == event.DataVersion &&
		current.CanonicalRecordSHA256 == event.CanonicalRecordSHA256 {
		s.storeRevisionLocked(event, revisionKey, StatusDuplicate)
		result.Status = StatusDuplicate
		result.CloudDataVersion = current.DataVersion
		result.CloudRevisionID = current.RevisionID
		return result
	}

	s.storeRevisionLocked(event, revisionKey, StatusSynced)
	s.entities[event.EntityID] = memoryEntity{
		EntityID:              event.EntityID,
		EntityType:            event.EntityType,
		DataVersion:           event.DataVersion,
		RevisionID:            event.RevisionID,
		CanonicalRecordSHA256: event.CanonicalRecordSHA256,
		UpdatedByDeviceID:     deviceID,
		Payload:               append(json.RawMessage(nil), event.Payload...),
		DeletedAt:             event.DeletedAt,
	}
	if hasContentIndex {
		s.contents[contentIndex.ContentID] = ContentObject{
			ContentID:      contentIndex.ContentID,
			FileSHA256:     contentIndex.FileSHA256,
			SizeBytes:      contentIndex.SizeBytes,
			LatestEntityID: event.EntityID,
			UpdatedAt:      time.Now().UTC(),
		}
	}
	if hasArchiveIndex {
		s.archives[archiveIndex.ArchiveSHA256] = ArchiveObject{
			ArchiveSHA256:  archiveIndex.ArchiveSHA256,
			ArchiveSize:    archiveIndex.ArchiveSize,
			RemotePath:     archiveIndex.RemotePath,
			RemoteVerified: archiveIndex.RemoteVerified,
			LatestEntityID: event.EntityID,
			UpdatedAt:      time.Now().UTC(),
		}
	}

	result.Status = StatusSynced
	return result
}

func (s *memoryStore) storeRevisionLocked(event RevisionEvent, revisionKey string, status string) {
	s.eventIDs[event.EventID] = revisionKey
	s.revisions[revisionKey] = memoryRevision{
		EventID:               event.EventID,
		EntityID:              event.EntityID,
		RevisionID:            event.RevisionID,
		DataVersion:           event.DataVersion,
		Status:                status,
		CanonicalRecordSHA256: event.CanonicalRecordSHA256,
		CreatedAt:             time.Now().UTC(),
	}
}

func (s *memoryStore) GetContent(_ context.Context, contentID string) (ContentObject, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.err != nil {
		return ContentObject{}, false, s.err
	}
	content, ok := s.contents[contentID]
	return content, ok, nil
}

func (s *memoryStore) GetArchive(_ context.Context, archiveSHA256 string) (ArchiveObject, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.err != nil {
		return ArchiveObject{}, false, s.err
	}
	archive, ok := s.archives[archiveSHA256]
	return archive, ok, nil
}

func (s *memoryStore) GetEntitySummary(_ context.Context, entityID string) (EntitySummary, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.err != nil {
		return EntitySummary{}, false, s.err
	}
	entity, ok := s.entities[entityID]
	if !ok {
		return EntitySummary{}, false, nil
	}

	summary := EntitySummary{
		EntityID:              entity.EntityID,
		EntityType:            entity.EntityType,
		DataVersion:           entity.DataVersion,
		RevisionID:            entity.RevisionID,
		CanonicalRecordSHA256: entity.CanonicalRecordSHA256,
		UpdatedByDeviceID:     entity.UpdatedByDeviceID,
		DeletedAt:             entity.DeletedAt,
	}
	for _, revision := range s.revisions {
		if revision.EntityID != entityID {
			continue
		}
		summary.RecentRevisionSummaries = append(summary.RecentRevisionSummaries, RevisionSummary{
			EventID:               revision.EventID,
			RevisionID:            revision.RevisionID,
			DataVersion:           revision.DataVersion,
			ApplyStatus:           revision.Status,
			CanonicalRecordSHA256: revision.CanonicalRecordSHA256,
			CreatedAt:             revision.CreatedAt,
		})
	}
	return summary, true, nil
}

func (s *memoryStore) ListBackupHistory(_ context.Context, deviceID string, limit int) ([]BackupHistoryEntity, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.err != nil {
		return nil, s.err
	}
	entities := make([]BackupHistoryEntity, 0)
	for _, entity := range s.entities {
		if entity.DeletedAt != nil || entity.UpdatedByDeviceID != deviceID {
			continue
		}
		switch entity.EntityType {
		case "backup_jobs", "backup_sources", "file_items", "folder_items", "content_objects", "content_references", "archives", "archive_members", "remote_objects":
		default:
			continue
		}
		entities = append(entities, BackupHistoryEntity{
			EntityID:              entity.EntityID,
			EntityType:            entity.EntityType,
			DataVersion:           entity.DataVersion,
			RevisionID:            entity.RevisionID,
			CanonicalRecordSHA256: entity.CanonicalRecordSHA256,
			UpdatedByDeviceID:     entity.UpdatedByDeviceID,
			Payload:               append(json.RawMessage(nil), entity.Payload...),
		})
		if len(entities) >= limit {
			break
		}
	}
	return entities, nil
}

func (s *memoryStore) Ping(_ context.Context) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	return s.err
}

func (s *memoryStore) CheckSchema(_ context.Context) (SchemaReadiness, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.err != nil {
		return SchemaReadiness{}, s.err
	}
	return SchemaReadiness{Ready: true}, nil
}

func (s *memoryStore) CreateBaiduAuthSession(_ context.Context, session BaiduAuthSession) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.err != nil {
		return s.err
	}
	s.baiduSessions[session.SessionID] = session
	s.baiduSessionStates[session.State] = session.SessionID
	return nil
}

func (s *memoryStore) GetBaiduAuthSession(_ context.Context, sessionID string) (BaiduAuthSession, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.err != nil {
		return BaiduAuthSession{}, false, s.err
	}
	session, ok := s.baiduSessions[sessionID]
	return session, ok, nil
}

func (s *memoryStore) GetBaiduAuthSessionByState(_ context.Context, state string) (BaiduAuthSession, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.err != nil {
		return BaiduAuthSession{}, false, s.err
	}
	sessionID, ok := s.baiduSessionStates[state]
	if !ok {
		return BaiduAuthSession{}, false, nil
	}
	session, ok := s.baiduSessions[sessionID]
	return session, ok, nil
}

func (s *memoryStore) MarkBaiduAuthSessionCallback(_ context.Context, state string, code string, errorCode string, errorDescription string) (BaiduAuthSession, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.err != nil {
		return BaiduAuthSession{}, false, s.err
	}
	sessionID, ok := s.baiduSessionStates[state]
	if !ok {
		return BaiduAuthSession{}, false, nil
	}
	session := s.baiduSessions[sessionID]
	session.AuthorizationCode = code
	session.ErrorCode = errorCode
	session.ErrorDescription = errorDescription
	if errorCode != "" {
		session.Status = BaiduAuthStatusFailed
	} else if code != "" {
		session.Status = BaiduAuthStatusAuthorized
	}
	s.baiduSessions[sessionID] = session
	return session, true, nil
}

func (s *memoryStore) CompleteBaiduAuthSession(_ context.Context, session BaiduAuthSession, account BaiduAccount, deviceID string) (BaiduAuthSession, BaiduAccount, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.err != nil {
		return BaiduAuthSession{}, BaiduAccount{}, s.err
	}
	existingAccountID, ok := s.baiduAccountByUID[account.BaiduUID]
	if ok {
		existing := s.baiduAccounts[existingAccountID]
		account.AccountID = existing.AccountID
	} else {
		s.baiduAccountByUID[account.BaiduUID] = account.AccountID
	}

	baseAccount := account
	baseAccount.TokenExpiresAt = time.Time{}
	baseAccount.EncryptionMethod = ""
	baseAccount.EncryptedToken = nil
	baseAccount.PrivateKeyHint = ""
	baseAccount.TokenVersion = 0
	baseAccount.LastVerifiedAt = nil
	baseAccount.LastVerifyStatus = "unknown"
	baseAccount.Selected = false
	s.baiduAccounts[account.AccountID] = baseAccount

	if s.baiduBindings[account.AccountID] == nil {
		s.baiduBindings[account.AccountID] = map[string]bool{}
	}
	s.baiduBindings[account.AccountID][deviceID] = true
	if s.baiduAuthorizations[account.AccountID] == nil {
		s.baiduAuthorizations[account.AccountID] = map[string]BaiduAccount{}
	}
	if existingAuth, ok := s.baiduAuthorizations[account.AccountID][deviceID]; ok {
		account.TokenVersion = existingAuth.TokenVersion + 1
	} else {
		account.TokenVersion = 1
	}
	account.DeviceID = deviceID
	account.Selected = true
	account.CurrentDevice = true
	s.baiduAuthorizations[account.AccountID][deviceID] = account

	now := time.Now().UTC()
	session.Status = BaiduAuthStatusCompleted
	session.CompletedAt = &now
	session.AccountID = account.AccountID
	session.DeviceCode = ""
	session.AuthorizationCode = ""
	s.baiduSessions[session.SessionID] = session
	return session, account, nil
}

func (s *memoryStore) ListBaiduAccounts(_ context.Context, deviceID string) ([]BaiduAccount, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.err != nil {
		return nil, s.err
	}
	accounts := make([]BaiduAccount, 0, len(s.baiduAccounts))
	for _, account := range s.baiduAccounts {
		listedDeviceIDs := map[string]bool{}
		if bindings := s.baiduBindings[account.AccountID]; bindings != nil {
			for boundDeviceID := range bindings {
				listedDeviceIDs[boundDeviceID] = true
			}
		}
		if deviceAccounts := s.baiduAuthorizations[account.AccountID]; deviceAccounts != nil {
			for authorizedDeviceID := range deviceAccounts {
				listedDeviceIDs[authorizedDeviceID] = true
			}
		}
		if len(listedDeviceIDs) == 0 {
			deviceAccount, _ := s.baiduAccountForDeviceLocked(account.AccountID, deviceID, false)
			accounts = append(accounts, deviceAccount)
			continue
		}
		for listedDeviceID := range listedDeviceIDs {
			deviceAccount, _ := s.baiduAccountForDeviceLocked(account.AccountID, listedDeviceID, false)
			deviceAccount.Selected = listedDeviceID == deviceID
			deviceAccount.CurrentDevice = listedDeviceID == deviceID
			if !deviceAccount.Selected {
				deviceAccount.Selected = false
				deviceAccount.CurrentDevice = false
			}
			if deviceAccount.DeviceID == "" {
				deviceAccount.DeviceID = listedDeviceID
			}
			accounts = append(accounts, deviceAccount)
		}
	}
	return accounts, nil
}

func (s *memoryStore) SelectBaiduAccount(_ context.Context, accountID string, deviceID string) (BaiduAccount, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.err != nil {
		return BaiduAccount{}, false, s.err
	}
	if _, ok := s.baiduAccounts[accountID]; !ok {
		return BaiduAccount{}, false, nil
	}
	if s.baiduBindings[accountID] == nil {
		s.baiduBindings[accountID] = map[string]bool{}
	}
	s.baiduBindings[accountID][deviceID] = true
	selected, _ := s.baiduAccountForDeviceLocked(accountID, deviceID, false)
	selected.DeviceID = deviceID
	selected.CurrentDevice = true
	return selected, true, nil
}

func (s *memoryStore) GetBaiduAccount(_ context.Context, accountID string, deviceID string) (BaiduAccount, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.err != nil {
		return BaiduAccount{}, false, s.err
	}
	account, ok := s.baiduAccountForDeviceLocked(accountID, deviceID, true)
	return account, ok, nil
}

func (s *memoryStore) UpdateBaiduAccountToken(_ context.Context, accountID string, deviceID string, expectedVersion int64, update BaiduAccount) (BaiduAccount, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.err != nil {
		return BaiduAccount{}, false, s.err
	}
	current, ok := s.baiduAccountForDeviceLocked(accountID, deviceID, true)
	if !ok {
		return BaiduAccount{}, false, nil
	}
	if current.TokenVersion != expectedVersion {
		return current, false, nil
	}
	update.AccountID = current.AccountID
	update.BaiduUID = current.BaiduUID
	update.BaiduUK = current.BaiduUK
	update.DisplayName = current.DisplayName
	update.Scope = current.Scope
	update.DeviceID = deviceID
	update.TokenVersion = current.TokenVersion + 1
	update.Selected = s.baiduBindings[accountID][deviceID]
	update.CurrentDevice = true
	s.baiduAuthorizations[accountID][deviceID] = update
	return update, true, nil
}

func (s *memoryStore) AcquireBaiduRefreshLease(_ context.Context, accountID string, deviceID string, leaseID string, durationSeconds int64) (BaiduRefreshLease, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.err != nil {
		return BaiduRefreshLease{}, false, s.err
	}
	if _, ok := s.baiduAccountForDeviceLocked(accountID, deviceID, true); !ok {
		return BaiduRefreshLease{}, false, nil
	}
	now := time.Now().UTC()
	leaseKey := baiduLeaseKey(accountID, deviceID)
	if existing, ok := s.baiduLeases[leaseKey]; ok && now.Before(existing.ExpiresAt) && existing.HolderDeviceID != deviceID {
		return existing, false, nil
	}
	lease := BaiduRefreshLease{
		AccountID:      accountID,
		LeaseID:        leaseID,
		HolderDeviceID: deviceID,
		ExpiresAt:      now.Add(time.Duration(durationSeconds) * time.Second),
	}
	s.baiduLeases[leaseKey] = lease
	return lease, true, nil
}

func (s *memoryStore) baiduAccountForDeviceLocked(accountID string, deviceID string, requireAuthorization bool) (BaiduAccount, bool) {
	account, ok := s.baiduAccounts[accountID]
	if !ok {
		return BaiduAccount{}, false
	}
	if deviceAccounts := s.baiduAuthorizations[accountID]; deviceAccounts != nil {
		if authorized, ok := deviceAccounts[deviceID]; ok && authorized.TokenVersion > 0 && len(authorized.EncryptedToken) > 0 {
			authorized.Selected = s.baiduBindings[accountID][deviceID]
			authorized.DeviceID = deviceID
			authorized.CurrentDevice = true
			return authorized, true
		}
	}
	if requireAuthorization {
		return BaiduAccount{}, false
	}
	account.DeviceID = deviceID
	account.Selected = s.baiduBindings[accountID][deviceID]
	account.CurrentDevice = account.Selected
	account.TokenExpiresAt = time.Time{}
	account.EncryptionMethod = ""
	account.EncryptedToken = nil
	account.PrivateKeyHint = ""
	account.TokenVersion = 0
	account.LastVerifiedAt = nil
	account.LastVerifyStatus = "unknown"
	return account, true
}

func baiduLeaseKey(accountID string, deviceID string) string {
	return accountID + "\x00" + deviceID
}
