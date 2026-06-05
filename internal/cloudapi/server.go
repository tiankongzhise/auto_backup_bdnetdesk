package cloudapi

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
)

type deviceContextKey struct{}

type Server struct {
	store  Store
	logger *slog.Logger
	now    func() time.Time
}

func NewServer(store Store, logger *slog.Logger) http.Handler {
	if logger == nil {
		logger = slog.Default()
	}

	server := &Server{
		store:  store,
		logger: logger,
		now:    time.Now,
	}
	return server.routes()
}

func (s *Server) routes() http.Handler {
	router := chi.NewRouter()
	router.Use(middleware.RequestID)
	router.Use(middleware.Recoverer)

	router.Route("/v1", func(r chi.Router) {
		r.Post("/devices/register", s.handleRegisterDevice)
		r.Get("/healthz", s.handleHealthz)
		r.Get("/readyz", s.handleReadyz)

		r.Group(func(authed chi.Router) {
			authed.Use(s.requireDevice)
			authed.Post("/sync/revisions", s.handleSyncRevisions)
			authed.Get("/contents/{content_id}", s.handleGetContent)
			authed.Get("/archives/{archive_sha256}", s.handleGetArchive)
			authed.Get("/reconcile/entities/{entity_id}", s.handleGetEntitySummary)
		})
	})

	return router
}

func (s *Server) handleRegisterDevice(w http.ResponseWriter, r *http.Request) {
	var req RegisterDeviceRequest
	if err := decodeJSON(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_json", err.Error())
		return
	}

	req.DeviceName = strings.TrimSpace(req.DeviceName)
	if req.DeviceName == "" {
		writeError(w, http.StatusBadRequest, "invalid_device_name", "device_name is required")
		return
	}

	deviceID, err := newDeviceID()
	if err != nil {
		s.logger.Error("failed to generate device id", "err", err)
		writeError(w, http.StatusInternalServerError, "internal_error", "failed to register device")
		return
	}
	token, err := newDeviceToken()
	if err != nil {
		s.logger.Error("failed to generate device token", "err", err)
		writeError(w, http.StatusInternalServerError, "internal_error", "failed to register device")
		return
	}

	device := Device{
		DeviceID:      deviceID,
		DeviceName:    req.DeviceName,
		Hostname:      strings.TrimSpace(req.Hostname),
		OSVersion:     strings.TrimSpace(req.OSVersion),
		ClientVersion: strings.TrimSpace(req.ClientVersion),
	}
	if err := s.store.RegisterDevice(r.Context(), device, hashToken(token)); err != nil {
		s.logger.Error("failed to persist device", "err", err)
		writeError(w, http.StatusInternalServerError, "internal_error", "failed to register device")
		return
	}

	writeJSON(w, http.StatusCreated, RegisterDeviceResponse{
		DeviceID:    deviceID,
		DeviceToken: token,
	})
}

func (s *Server) handleHealthz(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func (s *Server) handleReadyz(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
	defer cancel()

	if err := s.store.Ping(ctx); err != nil {
		writeError(w, http.StatusServiceUnavailable, "not_ready", "database is not ready")
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "ready"})
}

func (s *Server) handleSyncRevisions(w http.ResponseWriter, r *http.Request) {
	var req SyncRevisionsRequest
	if err := decodeJSON(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_json", err.Error())
		return
	}
	if len(req.Events) == 0 {
		writeError(w, http.StatusBadRequest, "empty_events", "events must not be empty")
		return
	}
	if len(req.Events) > 100 {
		writeError(w, http.StatusBadRequest, "too_many_events", "at most 100 events are accepted per request")
		return
	}

	results := make([]RevisionResult, len(req.Events))
	validEvents := make([]RevisionEvent, 0, len(req.Events))
	validPositions := make([]int, 0, len(req.Events))
	now := s.now().UTC()

	for i, event := range req.Events {
		if event.UpdatedAt == nil {
			event.UpdatedAt = &now
		}
		if err := validateRevisionEvent(event); err != nil {
			results[i] = RevisionResult{
				EventID:    event.EventID,
				EntityID:   event.EntityID,
				RevisionID: event.RevisionID,
				Status:     StatusRejected,
				Reason:     err.Error(),
			}
			continue
		}

		validPositions = append(validPositions, i)
		validEvents = append(validEvents, event)
	}

	if len(validEvents) > 0 {
		device := deviceFromContext(r.Context())
		storeResults, err := s.store.ApplyRevisions(r.Context(), device.DeviceID, validEvents)
		if err != nil {
			s.logger.Error("failed to apply revisions", "err", err, "device_id", device.DeviceID)
			writeError(w, http.StatusServiceUnavailable, "retryable_error", "cloud sync store is unavailable")
			return
		}

		for i, result := range storeResults {
			results[validPositions[i]] = result
		}
	}

	writeJSON(w, http.StatusOK, SyncRevisionsResponse{Results: results})
}

func (s *Server) handleGetContent(w http.ResponseWriter, r *http.Request) {
	contentID := strings.TrimSpace(chi.URLParam(r, "content_id"))
	if contentID == "" {
		writeError(w, http.StatusBadRequest, "invalid_content_id", "content_id is required")
		return
	}

	content, ok, err := s.store.GetContent(r.Context(), contentID)
	if err != nil {
		writeError(w, http.StatusServiceUnavailable, "retryable_error", "cloud store is unavailable")
		return
	}
	if !ok {
		writeError(w, http.StatusNotFound, "not_found", "content object not found")
		return
	}

	writeJSON(w, http.StatusOK, content)
}

func (s *Server) handleGetArchive(w http.ResponseWriter, r *http.Request) {
	archiveSHA256 := strings.TrimSpace(chi.URLParam(r, "archive_sha256"))
	if archiveSHA256 == "" {
		writeError(w, http.StatusBadRequest, "invalid_archive_sha256", "archive_sha256 is required")
		return
	}

	archive, ok, err := s.store.GetArchive(r.Context(), archiveSHA256)
	if err != nil {
		writeError(w, http.StatusServiceUnavailable, "retryable_error", "cloud store is unavailable")
		return
	}
	if !ok {
		writeError(w, http.StatusNotFound, "not_found", "archive object not found")
		return
	}

	writeJSON(w, http.StatusOK, archive)
}

func (s *Server) handleGetEntitySummary(w http.ResponseWriter, r *http.Request) {
	entityID := strings.TrimSpace(chi.URLParam(r, "entity_id"))
	if entityID == "" {
		writeError(w, http.StatusBadRequest, "invalid_entity_id", "entity_id is required")
		return
	}

	summary, ok, err := s.store.GetEntitySummary(r.Context(), entityID)
	if err != nil {
		writeError(w, http.StatusServiceUnavailable, "retryable_error", "cloud store is unavailable")
		return
	}
	if !ok {
		writeError(w, http.StatusNotFound, "not_found", "entity not found")
		return
	}

	writeJSON(w, http.StatusOK, summary)
}

func (s *Server) requireDevice(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		token, ok := bearerToken(r.Header.Get("Authorization"))
		if !ok {
			writeError(w, http.StatusUnauthorized, "unauthorized", "missing bearer token")
			return
		}

		device, ok, err := s.store.DeviceByTokenHash(r.Context(), hashToken(token))
		if err != nil {
			writeError(w, http.StatusServiceUnavailable, "retryable_error", "cloud store is unavailable")
			return
		}
		if !ok || device.Revoked {
			writeError(w, http.StatusUnauthorized, "unauthorized", "invalid bearer token")
			return
		}

		ctx := context.WithValue(r.Context(), deviceContextKey{}, device)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

func bearerToken(header string) (string, bool) {
	const prefix = "Bearer "
	if !strings.HasPrefix(header, prefix) {
		return "", false
	}
	token := strings.TrimSpace(strings.TrimPrefix(header, prefix))
	return token, token != ""
}

func deviceFromContext(ctx context.Context) Device {
	device, _ := ctx.Value(deviceContextKey{}).(Device)
	return device
}

func validateRevisionEvent(event RevisionEvent) error {
	if strings.TrimSpace(event.EventID) == "" {
		return errors.New("event_id is required")
	}
	if strings.TrimSpace(event.EntityType) == "" {
		return errors.New("entity_type is required")
	}
	if strings.TrimSpace(event.EntityID) == "" {
		return errors.New("entity_id is required")
	}
	if strings.TrimSpace(event.RevisionID) == "" {
		return errors.New("revision_id is required")
	}
	if event.SchemaVersion <= 0 {
		return errors.New("schema_version must be greater than zero")
	}
	if event.DataVersion <= 0 {
		return errors.New("data_version must be greater than zero")
	}
	if event.Operation != "upsert" && event.Operation != "delete" {
		return errors.New("operation must be upsert or delete")
	}
	if !isHexSHA256(event.CanonicalRecordSHA256) {
		return errors.New("canonical_record_sha256 must be 64 lowercase hex characters")
	}
	if len(event.Payload) == 0 || !json.Valid(event.Payload) {
		return errors.New("payload must be valid JSON")
	}
	if !utf8.Valid(event.Payload) {
		return errors.New("payload must be valid UTF-8")
	}
	return nil
}

func isHexSHA256(value string) bool {
	if len(value) != 64 {
		return false
	}
	for _, char := range value {
		if char >= '0' && char <= '9' {
			continue
		}
		if char >= 'a' && char <= 'f' {
			continue
		}
		return false
	}
	return true
}

func decodeJSON(r *http.Request, out any) error {
	decoder := json.NewDecoder(http.MaxBytesReader(nil, r.Body, 2<<20))
	decoder.DisallowUnknownFields()
	return decoder.Decode(out)
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func writeError(w http.ResponseWriter, status int, code, message string) {
	writeJSON(w, status, map[string]string{
		"error":   code,
		"message": message,
	})
}
