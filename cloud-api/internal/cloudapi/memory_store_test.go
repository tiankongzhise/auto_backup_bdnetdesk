package cloudapi

import (
	"context"
	"sync"
	"time"
)

type memoryStore struct {
	mu                 sync.Mutex
	devices            map[string]Device
	tokenHashes        map[string]string
	entities           map[string]memoryEntity
	revisions          map[string]memoryRevision
	eventIDs           map[string]string
	contents           map[string]ContentObject
	archives           map[string]ArchiveObject
	baiduSessions      map[string]BaiduAuthSession
	baiduSessionStates map[string]string
	baiduAccounts      map[string]BaiduAccount
	baiduAccountByUID  map[string]string
	baiduBindings      map[string]map[string]bool
	baiduLeases        map[string]BaiduRefreshLease
	err                error
}

type memoryEntity struct {
	EntityID              string
	EntityType            string
	DataVersion           int64
	RevisionID            string
	CanonicalRecordSHA256 string
	UpdatedByDeviceID     string
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
		devices:            map[string]Device{},
		tokenHashes:        map[string]string{},
		entities:           map[string]memoryEntity{},
		revisions:          map[string]memoryRevision{},
		eventIDs:           map[string]string{},
		contents:           map[string]ContentObject{},
		archives:           map[string]ArchiveObject{},
		baiduSessions:      map[string]BaiduAuthSession{},
		baiduSessionStates: map[string]string{},
		baiduAccounts:      map[string]BaiduAccount{},
		baiduAccountByUID:  map[string]string{},
		baiduBindings:      map[string]map[string]bool{},
		baiduLeases:        map[string]BaiduRefreshLease{},
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
		account.TokenVersion = existing.TokenVersion + 1
	} else {
		s.baiduAccountByUID[account.BaiduUID] = account.AccountID
	}
	account.Selected = true
	s.baiduAccounts[account.AccountID] = account
	if s.baiduBindings[account.AccountID] == nil {
		s.baiduBindings[account.AccountID] = map[string]bool{}
	}
	s.baiduBindings[account.AccountID][deviceID] = true

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
		account.Selected = s.baiduBindings[account.AccountID][deviceID]
		accounts = append(accounts, account)
	}
	return accounts, nil
}

func (s *memoryStore) SelectBaiduAccount(_ context.Context, accountID string, deviceID string) (BaiduAccount, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.err != nil {
		return BaiduAccount{}, false, s.err
	}
	account, ok := s.baiduAccounts[accountID]
	if !ok {
		return BaiduAccount{}, false, nil
	}
	if s.baiduBindings[accountID] == nil {
		s.baiduBindings[accountID] = map[string]bool{}
	}
	s.baiduBindings[accountID][deviceID] = true
	account.Selected = true
	return account, true, nil
}

func (s *memoryStore) GetBaiduAccount(_ context.Context, accountID string, deviceID string) (BaiduAccount, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.err != nil {
		return BaiduAccount{}, false, s.err
	}
	account, ok := s.baiduAccounts[accountID]
	if !ok {
		return BaiduAccount{}, false, nil
	}
	account.Selected = s.baiduBindings[accountID][deviceID]
	return account, true, nil
}

func (s *memoryStore) UpdateBaiduAccountToken(_ context.Context, accountID string, deviceID string, expectedVersion int64, update BaiduAccount) (BaiduAccount, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.err != nil {
		return BaiduAccount{}, false, s.err
	}
	current, ok := s.baiduAccounts[accountID]
	if !ok {
		return BaiduAccount{}, false, nil
	}
	if current.TokenVersion != expectedVersion {
		current.Selected = s.baiduBindings[accountID][deviceID]
		return current, false, nil
	}
	update.AccountID = current.AccountID
	update.BaiduUID = current.BaiduUID
	update.BaiduUK = current.BaiduUK
	update.DisplayName = current.DisplayName
	update.Scope = current.Scope
	update.TokenVersion = current.TokenVersion + 1
	update.Selected = s.baiduBindings[accountID][deviceID]
	s.baiduAccounts[accountID] = update
	return update, true, nil
}

func (s *memoryStore) AcquireBaiduRefreshLease(_ context.Context, accountID string, deviceID string, leaseID string, durationSeconds int64) (BaiduRefreshLease, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.err != nil {
		return BaiduRefreshLease{}, false, s.err
	}
	if _, ok := s.baiduAccounts[accountID]; !ok {
		return BaiduRefreshLease{}, false, nil
	}
	now := time.Now().UTC()
	if existing, ok := s.baiduLeases[accountID]; ok && now.Before(existing.ExpiresAt) && existing.HolderDeviceID != deviceID {
		return existing, false, nil
	}
	lease := BaiduRefreshLease{
		AccountID:      accountID,
		LeaseID:        leaseID,
		HolderDeviceID: deviceID,
		ExpiresAt:      now.Add(time.Duration(durationSeconds) * time.Second),
	}
	s.baiduLeases[accountID] = lease
	return lease, true, nil
}
