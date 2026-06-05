package cloudapi

import (
	"context"
	"sync"
	"time"
)

type memoryStore struct {
	mu          sync.Mutex
	devices     map[string]Device
	tokenHashes map[string]string
	entities    map[string]memoryEntity
	revisions   map[string]memoryRevision
	eventIDs    map[string]string
	contents    map[string]ContentObject
	archives    map[string]ArchiveObject
	err         error
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
		devices:     map[string]Device{},
		tokenHashes: map[string]string{},
		entities:    map[string]memoryEntity{},
		revisions:   map[string]memoryRevision{},
		eventIDs:    map[string]string{},
		contents:    map[string]ContentObject{},
		archives:    map[string]ArchiveObject{},
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
