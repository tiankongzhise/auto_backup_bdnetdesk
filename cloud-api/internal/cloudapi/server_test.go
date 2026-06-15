package cloudapi

import (
	"bytes"
	"encoding/json"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"
)

const testSHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
const secondSHA = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

func TestRegisterDeviceAndAuth(t *testing.T) {
	store := newMemoryStore()
	handler := NewServer(store, slog.New(slog.NewTextHandler(bytes.NewBuffer(nil), nil)))

	registerResp := registerDevice(t, handler)
	if registerResp.DeviceID == "" {
		t.Fatal("expected device id")
	}
	if registerResp.DeviceToken == "" {
		t.Fatal("expected device token")
	}

	req := httptest.NewRequest(http.MethodGet, "/v1/contents/missing", nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401 without bearer token, got %d", rec.Code)
	}

	req = httptest.NewRequest(http.MethodGet, "/v1/contents/missing", nil)
	req.Header.Set("Authorization", "Bearer "+registerResp.DeviceToken)
	rec = httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	if rec.Code != http.StatusNotFound {
		t.Fatalf("expected authenticated 404 for missing content, got %d", rec.Code)
	}
}

func TestSyncRevisionIdempotentDuplicate(t *testing.T) {
	store := newMemoryStore()
	handler := NewServer(store, slog.New(slog.NewTextHandler(bytes.NewBuffer(nil), nil)))
	token := registerDevice(t, handler).DeviceToken

	event := RevisionEvent{
		EventID:               "evt-1",
		EntityType:            "content_objects",
		EntityID:              "entity-content-1",
		RevisionID:            "rev-1",
		SchemaVersion:         1,
		DataVersion:           1,
		Operation:             "upsert",
		CanonicalRecordSHA256: testSHA,
		Payload: json.RawMessage(`{
			"content_id":"content-1",
			"file_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
			"size_bytes":42
		}`),
	}

	first := syncEvents(t, handler, token, event)
	if first.Results[0].Status != StatusSynced {
		t.Fatalf("expected synced, got %#v", first.Results[0])
	}

	second := syncEvents(t, handler, token, event)
	if second.Results[0].Status != StatusDuplicate {
		t.Fatalf("expected duplicate, got %#v", second.Results[0])
	}

	req := httptest.NewRequest(http.MethodGet, "/v1/contents/content-1", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected content query 200, got %d: %s", rec.Code, rec.Body.String())
	}
}

func TestSyncRevisionConflict(t *testing.T) {
	store := newMemoryStore()
	handler := NewServer(store, slog.New(slog.NewTextHandler(bytes.NewBuffer(nil), nil)))
	token := registerDevice(t, handler).DeviceToken

	first := RevisionEvent{
		EventID:               "evt-first",
		EntityType:            "backup_jobs",
		EntityID:              "job-1",
		RevisionID:            "rev-2",
		SchemaVersion:         1,
		DataVersion:           2,
		Operation:             "upsert",
		CanonicalRecordSHA256: testSHA,
		Payload:               json.RawMessage(`{"job_id":"job-1","status":"completed"}`),
	}
	if got := syncEvents(t, handler, token, first).Results[0]; got.Status != StatusSynced {
		t.Fatalf("expected initial synced, got %#v", got)
	}

	stale := RevisionEvent{
		EventID:               "evt-stale",
		EntityType:            "backup_jobs",
		EntityID:              "job-1",
		RevisionID:            "rev-1",
		SchemaVersion:         1,
		DataVersion:           1,
		Operation:             "upsert",
		CanonicalRecordSHA256: secondSHA,
		Payload:               json.RawMessage(`{"job_id":"job-1","status":"running"}`),
	}
	got := syncEvents(t, handler, token, stale).Results[0]
	if got.Status != StatusConflict {
		t.Fatalf("expected conflict, got %#v", got)
	}
	if got.CloudDataVersion != 2 || got.CloudRevisionID != "rev-2" {
		t.Fatalf("expected cloud conflict metadata, got %#v", got)
	}
}

func TestArchiveQueryAfterSync(t *testing.T) {
	store := newMemoryStore()
	handler := NewServer(store, slog.New(slog.NewTextHandler(bytes.NewBuffer(nil), nil)))
	token := registerDevice(t, handler).DeviceToken

	event := RevisionEvent{
		EventID:               "evt-archive",
		EntityType:            "archives",
		EntityID:              "archive-entity-1",
		RevisionID:            "rev-archive-1",
		SchemaVersion:         1,
		DataVersion:           1,
		Operation:             "upsert",
		CanonicalRecordSHA256: testSHA,
		Payload: json.RawMessage(`{
			"archive_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
			"archive_size":1024,
			"remote_path":"/apps/app/backups/2026/06/05/dev/job/archives/000001.7z",
			"remote_verified":true
		}`),
	}
	if got := syncEvents(t, handler, token, event).Results[0]; got.Status != StatusSynced {
		t.Fatalf("expected synced, got %#v", got)
	}

	req := httptest.NewRequest(http.MethodGet, "/v1/archives/"+testSHA, nil)
	req.Header.Set("Authorization", "Bearer "+token)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected archive query 200, got %d: %s", rec.Code, rec.Body.String())
	}
}

func TestInvalidContentPayloadRejected(t *testing.T) {
	store := newMemoryStore()
	handler := NewServer(store, slog.New(slog.NewTextHandler(bytes.NewBuffer(nil), nil)))
	token := registerDevice(t, handler).DeviceToken

	event := RevisionEvent{
		EventID:               "evt-invalid-content",
		EntityType:            "content_objects",
		EntityID:              "entity-invalid-content",
		RevisionID:            "rev-invalid-content",
		SchemaVersion:         1,
		DataVersion:           1,
		Operation:             "upsert",
		CanonicalRecordSHA256: testSHA,
		Payload:               json.RawMessage(`{"content_id":"content-missing-size","file_sha256":"` + testSHA + `"}`),
	}

	got := syncEvents(t, handler, token, event).Results[0]
	if got.Status != StatusRejected {
		t.Fatalf("expected rejected, got %#v", got)
	}
}

func TestListBackupsReturnsOnlyAuthenticatedDeviceHistory(t *testing.T) {
	store := newMemoryStore()
	handler := NewServer(store, slog.New(slog.NewTextHandler(bytes.NewBuffer(nil), nil)))
	first := registerDevice(t, handler)
	second := registerDevice(t, handler)

	firstEvent := RevisionEvent{
		EventID:               "evt-history-first",
		EntityType:            "backup_jobs",
		EntityID:              "backup_job_job-first",
		RevisionID:            "rev-history-first",
		SchemaVersion:         1,
		DataVersion:           1,
		Operation:             "upsert",
		CanonicalRecordSHA256: testSHA,
		Payload:               json.RawMessage(`{"backup_job_id":"job-first","device_id":"` + first.DeviceID + `","status":"completed"}`),
	}
	secondEvent := RevisionEvent{
		EventID:               "evt-history-second",
		EntityType:            "backup_jobs",
		EntityID:              "backup_job_job-second",
		RevisionID:            "rev-history-second",
		SchemaVersion:         1,
		DataVersion:           1,
		Operation:             "upsert",
		CanonicalRecordSHA256: secondSHA,
		Payload:               json.RawMessage(`{"backup_job_id":"job-second","device_id":"` + second.DeviceID + `","status":"completed"}`),
	}
	if got := syncEvents(t, handler, first.DeviceToken, firstEvent).Results[0]; got.Status != StatusSynced {
		t.Fatalf("expected first synced, got %#v", got)
	}
	if got := syncEvents(t, handler, second.DeviceToken, secondEvent).Results[0]; got.Status != StatusSynced {
		t.Fatalf("expected second synced, got %#v", got)
	}

	req := httptest.NewRequest(http.MethodGet, "/v1/backups?device_id=current&limit=10", nil)
	req.Header.Set("Authorization", "Bearer "+first.DeviceToken)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected history 200, got %d: %s", rec.Code, rec.Body.String())
	}
	var resp BackupHistoryResponse
	if err := json.NewDecoder(rec.Body).Decode(&resp); err != nil {
		t.Fatalf("decode history response: %v", err)
	}
	if resp.DeviceID != first.DeviceID {
		t.Fatalf("expected first device id, got %s", resp.DeviceID)
	}
	if len(resp.Entities) != 1 || resp.Entities[0].EntityID != firstEvent.EntityID {
		t.Fatalf("expected only first device history, got %#v", resp.Entities)
	}

	forbidden := httptest.NewRequest(http.MethodGet, "/v1/backups?device_id="+second.DeviceID, nil)
	forbidden.Header.Set("Authorization", "Bearer "+first.DeviceToken)
	forbiddenRec := httptest.NewRecorder()
	handler.ServeHTTP(forbiddenRec, forbidden)
	if forbiddenRec.Code != http.StatusForbidden {
		t.Fatalf("expected forbidden cross-device history, got %d: %s", forbiddenRec.Code, forbiddenRec.Body.String())
	}
}

func registerDevice(t *testing.T, handler http.Handler) RegisterDeviceResponse {
	t.Helper()

	body := bytes.NewBufferString(`{
		"device_name":"dev box",
		"hostname":"host",
		"os_version":"Windows",
		"client_version":"v1.3-test"
	}`)
	req := httptest.NewRequest(http.MethodPost, "/v1/devices/register", body)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	if rec.Code != http.StatusCreated {
		t.Fatalf("register expected 201, got %d: %s", rec.Code, rec.Body.String())
	}

	var resp RegisterDeviceResponse
	if err := json.NewDecoder(rec.Body).Decode(&resp); err != nil {
		t.Fatalf("decode register response: %v", err)
	}
	return resp
}

func syncEvents(t *testing.T, handler http.Handler, token string, events ...RevisionEvent) SyncRevisionsResponse {
	t.Helper()

	payload, err := json.Marshal(SyncRevisionsRequest{Events: events})
	if err != nil {
		t.Fatalf("marshal sync request: %v", err)
	}

	req := httptest.NewRequest(http.MethodPost, "/v1/sync/revisions", bytes.NewReader(payload))
	req.Header.Set("Authorization", "Bearer "+token)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("sync expected 200, got %d: %s", rec.Code, rec.Body.String())
	}

	var resp SyncRevisionsResponse
	if err := json.NewDecoder(rec.Body).Decode(&resp); err != nil {
		t.Fatalf("decode sync response: %v", err)
	}
	if len(resp.Results) != len(events) {
		t.Fatalf("expected %d results, got %d", len(events), len(resp.Results))
	}
	return resp
}
