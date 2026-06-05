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
	store            Store
	logger           *slog.Logger
	now              func() time.Time
	baiduOAuthConfig BaiduOAuthConfig
	baiduOAuthClient BaiduOAuthClient
}

type ServerOption func(*Server)

func WithBaiduOAuthConfig(cfg BaiduOAuthConfig) ServerOption {
	return func(s *Server) {
		s.baiduOAuthConfig = cfg
	}
}

func WithBaiduOAuthClient(client BaiduOAuthClient) ServerOption {
	return func(s *Server) {
		s.baiduOAuthClient = client
	}
}

func NewServer(store Store, logger *slog.Logger, opts ...ServerOption) http.Handler {
	if logger == nil {
		logger = slog.Default()
	}

	server := &Server{
		store:            store,
		logger:           logger,
		now:              time.Now,
		baiduOAuthConfig: DefaultBaiduOAuthConfig(),
	}
	for _, opt := range opts {
		opt(server)
	}
	if server.baiduOAuthClient == nil {
		server.baiduOAuthClient = NewHTTPBaiduOAuthClient(server.baiduOAuthConfig)
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
		r.Get("/baidu/oauth/callback", s.handleBaiduOAuthCallback)

		r.Group(func(authed chi.Router) {
			authed.Use(s.requireDevice)
			authed.Post("/sync/revisions", s.handleSyncRevisions)
			authed.Get("/contents/{content_id}", s.handleGetContent)
			authed.Get("/archives/{archive_sha256}", s.handleGetArchive)
			authed.Get("/reconcile/entities/{entity_id}", s.handleGetEntitySummary)
			authed.Post("/baidu/auth/sessions", s.handleCreateBaiduAuthSession)
			authed.Get("/baidu/auth/sessions/{session_id}", s.handleGetBaiduAuthSession)
			authed.Post("/baidu/auth/sessions/{session_id}/complete", s.handleCompleteBaiduAuthSession)
			authed.Get("/baidu/accounts", s.handleListBaiduAccounts)
			authed.Post("/baidu/accounts/{account_id}/select", s.handleSelectBaiduAccount)
			authed.Get("/baidu/accounts/{account_id}/token", s.handleGetBaiduToken)
			authed.Put("/baidu/accounts/{account_id}/token", s.handleUpdateBaiduToken)
			authed.Post("/baidu/accounts/{account_id}/refresh-lease", s.handleAcquireBaiduRefreshLease)
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

func (s *Server) handleCreateBaiduAuthSession(w http.ResponseWriter, r *http.Request) {
	var req CreateBaiduAuthSessionRequest
	if err := decodeJSON(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_json", err.Error())
		return
	}

	flow := strings.TrimSpace(req.Flow)
	if flow == "" {
		flow = BaiduAuthFlowDeviceCode
	}
	if flow != BaiduAuthFlowDeviceCode && flow != BaiduAuthFlowAuthorizationCode {
		writeError(w, http.StatusBadRequest, "invalid_flow", "flow must be device_code or authorization_code")
		return
	}
	encryptionMethod := strings.TrimSpace(req.EncryptionMethod)
	if encryptionMethod == "" {
		encryptionMethod = BaiduEncryptionPassword
	}
	if err := validateBaiduEncryptionRequest(encryptionMethod, req.RSAPublicKeyPEM); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_encryption", err.Error())
		return
	}

	sessionID, err := newOpaqueID("bauth")
	if err != nil {
		writeError(w, http.StatusInternalServerError, "internal_error", "failed to create authorization session")
		return
	}
	state, err := newOpaqueID("bstate")
	if err != nil {
		writeError(w, http.StatusInternalServerError, "internal_error", "failed to create authorization session")
		return
	}

	device := deviceFromContext(r.Context())
	now := s.now().UTC()
	session := BaiduAuthSession{
		SessionID:           sessionID,
		Flow:                flow,
		Status:              BaiduAuthStatusPending,
		RequestedByDeviceID: device.DeviceID,
		State:               state,
		Scope:               s.baiduOAuthConfig.Scope,
		EncryptionMethod:    encryptionMethod,
		RSAPublicKeyPEM:     strings.TrimSpace(req.RSAPublicKeyPEM),
		PrivateKeyHint:      strings.TrimSpace(req.PrivateKeyHint),
		ExpiresAt:           now.Add(10 * time.Minute),
	}

	if flow == BaiduAuthFlowDeviceCode {
		start, err := s.baiduOAuthClient.StartDeviceAuth(r.Context(), session.Scope)
		if err != nil {
			writeError(w, http.StatusBadGateway, "baidu_oauth_unavailable", "failed to start baidu device authorization")
			return
		}
		session.DeviceCode = start.DeviceCode
		session.UserCode = start.UserCode
		session.VerificationURL = start.VerificationURL
		session.QRCodeURL = start.QRCodeURL
		if start.ExpiresIn > 0 {
			session.ExpiresAt = now.Add(time.Duration(start.ExpiresIn) * time.Second)
		}
	} else {
		if strings.TrimSpace(s.baiduOAuthConfig.AppKey) == "" || strings.TrimSpace(s.baiduOAuthConfig.RedirectURI) == "" {
			writeError(w, http.StatusBadGateway, "baidu_oauth_unavailable", "baidu authorization code flow is not configured")
			return
		}
		session.AuthURL = s.buildBaiduAuthorizeURL(state, session.Scope)
	}

	if err := s.store.CreateBaiduAuthSession(r.Context(), session); err != nil {
		s.logger.Error("failed to create baidu auth session", "err", err, "device_id", device.DeviceID)
		writeError(w, http.StatusServiceUnavailable, "retryable_error", "cloud store is unavailable")
		return
	}

	writeJSON(w, http.StatusCreated, toBaiduAuthSessionResponse(session))
}

func (s *Server) handleGetBaiduAuthSession(w http.ResponseWriter, r *http.Request) {
	sessionID := strings.TrimSpace(chi.URLParam(r, "session_id"))
	if sessionID == "" {
		writeError(w, http.StatusBadRequest, "invalid_session_id", "session_id is required")
		return
	}
	session, ok, err := s.store.GetBaiduAuthSession(r.Context(), sessionID)
	if err != nil {
		writeError(w, http.StatusServiceUnavailable, "retryable_error", "cloud store is unavailable")
		return
	}
	if !ok {
		writeError(w, http.StatusNotFound, "not_found", "baidu authorization session not found")
		return
	}
	if session.Status == BaiduAuthStatusPending && s.now().UTC().After(session.ExpiresAt) {
		session.Status = BaiduAuthStatusExpired
	}
	writeJSON(w, http.StatusOK, toBaiduAuthSessionResponse(session))
}

func (s *Server) handleCompleteBaiduAuthSession(w http.ResponseWriter, r *http.Request) {
	sessionID := strings.TrimSpace(chi.URLParam(r, "session_id"))
	if sessionID == "" {
		writeError(w, http.StatusBadRequest, "invalid_session_id", "session_id is required")
		return
	}
	var req CompleteBaiduAuthSessionRequest
	if err := decodeJSON(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_json", err.Error())
		return
	}

	session, ok, err := s.store.GetBaiduAuthSession(r.Context(), sessionID)
	if err != nil {
		writeError(w, http.StatusServiceUnavailable, "retryable_error", "cloud store is unavailable")
		return
	}
	if !ok {
		writeError(w, http.StatusNotFound, "not_found", "baidu authorization session not found")
		return
	}
	if session.Status == BaiduAuthStatusCompleted && session.AccountID != "" {
		account, ok, err := s.store.GetBaiduAccount(r.Context(), session.AccountID, deviceFromContext(r.Context()).DeviceID)
		if err != nil {
			writeError(w, http.StatusServiceUnavailable, "retryable_error", "cloud store is unavailable")
			return
		}
		if ok {
			writeJSON(w, http.StatusOK, CompleteBaiduAuthSessionResponse{
				Session: toBaiduAuthSessionResponse(session),
				Account: toBaiduAccountResponse(account),
				Token:   toBaiduEncryptedToken(account),
			})
			return
		}
	}
	if s.now().UTC().After(session.ExpiresAt) {
		writeError(w, http.StatusGone, "session_expired", "baidu authorization session has expired")
		return
	}

	tokenSet, err := s.exchangeBaiduSessionToken(r.Context(), session)
	if err != nil {
		writeError(w, http.StatusConflict, "authorization_pending", "baidu authorization is not ready")
		return
	}
	userInfo, err := s.baiduOAuthClient.GetUserInfo(r.Context(), tokenSet.AccessToken)
	if err != nil {
		writeError(w, http.StatusBadGateway, "baidu_userinfo_unavailable", "failed to get baidu account information")
		return
	}

	expiresAt := s.now().UTC().Add(time.Duration(tokenSet.ExpiresIn) * time.Second)
	encryptedToken, privateKeyHint, err := s.encryptBaiduTokenForSession(tokenSet, expiresAt, session, req)
	if err != nil {
		writeError(w, http.StatusBadRequest, "invalid_encryption", err.Error())
		return
	}

	accountID, err := newOpaqueID("bacc")
	if err != nil {
		writeError(w, http.StatusInternalServerError, "internal_error", "failed to create baidu account")
		return
	}
	account := BaiduAccount{
		AccountID:        accountID,
		BaiduUID:         userInfo.UID,
		BaiduUK:          userInfo.UK,
		DisplayName:      userInfo.DisplayName,
		Scope:            firstNonEmpty(tokenSet.Scope, session.Scope),
		TokenExpiresAt:   expiresAt,
		EncryptionMethod: session.EncryptionMethod,
		EncryptedToken:   encryptedToken,
		PrivateKeyHint:   privateKeyHint,
		TokenVersion:     1,
		LastVerifiedAt:   ptrTime(s.now().UTC()),
		LastVerifyStatus: "valid",
	}
	device := deviceFromContext(r.Context())
	completedSession, savedAccount, err := s.store.CompleteBaiduAuthSession(r.Context(), session, account, device.DeviceID)
	if err != nil {
		s.logger.Error("failed to complete baidu auth session", "err", err, "device_id", device.DeviceID)
		writeError(w, http.StatusServiceUnavailable, "retryable_error", "cloud store is unavailable")
		return
	}

	writeJSON(w, http.StatusOK, CompleteBaiduAuthSessionResponse{
		Session: toBaiduAuthSessionResponse(completedSession),
		Account: toBaiduAccountResponse(savedAccount),
		Token:   toBaiduEncryptedToken(savedAccount),
	})
}

func (s *Server) handleBaiduOAuthCallback(w http.ResponseWriter, r *http.Request) {
	state := strings.TrimSpace(r.URL.Query().Get("state"))
	code := strings.TrimSpace(r.URL.Query().Get("code"))
	errorCode := strings.TrimSpace(r.URL.Query().Get("error"))
	errorDescription := strings.TrimSpace(r.URL.Query().Get("error_description"))
	if state == "" {
		writeError(w, http.StatusBadRequest, "invalid_state", "state is required")
		return
	}
	session, ok, err := s.store.MarkBaiduAuthSessionCallback(r.Context(), state, code, errorCode, errorDescription)
	if err != nil {
		writeError(w, http.StatusServiceUnavailable, "retryable_error", "cloud store is unavailable")
		return
	}
	if !ok {
		writeError(w, http.StatusNotFound, "not_found", "baidu authorization session not found")
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{
		"status":     session.Status,
		"session_id": session.SessionID,
		"message":    "baidu authorization callback recorded; return to the client to finish encryption",
	})
}

func (s *Server) handleListBaiduAccounts(w http.ResponseWriter, r *http.Request) {
	device := deviceFromContext(r.Context())
	accounts, err := s.store.ListBaiduAccounts(r.Context(), device.DeviceID)
	if err != nil {
		writeError(w, http.StatusServiceUnavailable, "retryable_error", "cloud store is unavailable")
		return
	}
	resp := ListBaiduAccountsResponse{Accounts: make([]BaiduAccountResponse, 0, len(accounts))}
	for _, account := range accounts {
		resp.Accounts = append(resp.Accounts, toBaiduAccountResponse(account))
	}
	writeJSON(w, http.StatusOK, resp)
}

func (s *Server) handleSelectBaiduAccount(w http.ResponseWriter, r *http.Request) {
	accountID := strings.TrimSpace(chi.URLParam(r, "account_id"))
	if accountID == "" {
		writeError(w, http.StatusBadRequest, "invalid_account_id", "account_id is required")
		return
	}
	device := deviceFromContext(r.Context())
	account, ok, err := s.store.SelectBaiduAccount(r.Context(), accountID, device.DeviceID)
	if err != nil {
		writeError(w, http.StatusServiceUnavailable, "retryable_error", "cloud store is unavailable")
		return
	}
	if !ok {
		writeError(w, http.StatusNotFound, "not_found", "baidu account not found")
		return
	}
	writeJSON(w, http.StatusOK, toBaiduAccountResponse(account))
}

func (s *Server) handleGetBaiduToken(w http.ResponseWriter, r *http.Request) {
	accountID := strings.TrimSpace(chi.URLParam(r, "account_id"))
	if accountID == "" {
		writeError(w, http.StatusBadRequest, "invalid_account_id", "account_id is required")
		return
	}
	device := deviceFromContext(r.Context())
	account, ok, err := s.store.GetBaiduAccount(r.Context(), accountID, device.DeviceID)
	if err != nil {
		writeError(w, http.StatusServiceUnavailable, "retryable_error", "cloud store is unavailable")
		return
	}
	if !ok {
		writeError(w, http.StatusNotFound, "not_found", "baidu account not found")
		return
	}
	writeJSON(w, http.StatusOK, toBaiduEncryptedToken(account))
}

func (s *Server) handleUpdateBaiduToken(w http.ResponseWriter, r *http.Request) {
	accountID := strings.TrimSpace(chi.URLParam(r, "account_id"))
	if accountID == "" {
		writeError(w, http.StatusBadRequest, "invalid_account_id", "account_id is required")
		return
	}
	var req UpdateBaiduTokenRequest
	if err := decodeJSON(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_json", err.Error())
		return
	}
	if req.ExpectedTokenVersion <= 0 {
		writeError(w, http.StatusBadRequest, "invalid_token_version", "expected_token_version must be greater than zero")
		return
	}
	if err := validateEncryptedTokenUpdate(req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_token", err.Error())
		return
	}

	device := deviceFromContext(r.Context())
	existing, ok, err := s.store.GetBaiduAccount(r.Context(), accountID, device.DeviceID)
	if err != nil {
		writeError(w, http.StatusServiceUnavailable, "retryable_error", "cloud store is unavailable")
		return
	}
	if !ok {
		writeError(w, http.StatusNotFound, "not_found", "baidu account not found")
		return
	}
	update := existing
	update.TokenExpiresAt = req.TokenExpiresAt.UTC()
	update.EncryptionMethod = req.EncryptionMethod
	update.EncryptedToken = req.EncryptedToken
	update.PrivateKeyHint = strings.TrimSpace(req.PrivateKeyHint)
	if strings.TrimSpace(req.LastVerifyStatus) != "" {
		update.LastVerifyStatus = strings.TrimSpace(req.LastVerifyStatus)
	}
	now := s.now().UTC()
	update.LastVerifiedAt = &now

	account, updated, err := s.store.UpdateBaiduAccountToken(r.Context(), accountID, device.DeviceID, req.ExpectedTokenVersion, update)
	if err != nil {
		writeError(w, http.StatusServiceUnavailable, "retryable_error", "cloud store is unavailable")
		return
	}
	if !updated {
		writeError(w, http.StatusConflict, "token_version_conflict", "baidu account token version has changed")
		return
	}
	writeJSON(w, http.StatusOK, toBaiduEncryptedToken(account))
}

func (s *Server) handleAcquireBaiduRefreshLease(w http.ResponseWriter, r *http.Request) {
	accountID := strings.TrimSpace(chi.URLParam(r, "account_id"))
	if accountID == "" {
		writeError(w, http.StatusBadRequest, "invalid_account_id", "account_id is required")
		return
	}
	var req BaiduRefreshLeaseRequest
	if err := decodeJSON(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_json", err.Error())
		return
	}
	duration := req.DurationSeconds
	if duration <= 0 {
		duration = 300
	}
	if duration < 30 {
		duration = 30
	}
	if duration > 900 {
		duration = 900
	}
	leaseID := strings.TrimSpace(req.LeaseID)
	if leaseID == "" {
		var err error
		leaseID, err = newOpaqueID("blease")
		if err != nil {
			writeError(w, http.StatusInternalServerError, "internal_error", "failed to create refresh lease")
			return
		}
	}
	device := deviceFromContext(r.Context())
	if _, ok, err := s.store.GetBaiduAccount(r.Context(), accountID, device.DeviceID); err != nil {
		writeError(w, http.StatusServiceUnavailable, "retryable_error", "cloud store is unavailable")
		return
	} else if !ok {
		writeError(w, http.StatusNotFound, "not_found", "baidu account not found")
		return
	}
	lease, acquired, err := s.store.AcquireBaiduRefreshLease(r.Context(), accountID, device.DeviceID, leaseID, duration)
	if err != nil {
		writeError(w, http.StatusServiceUnavailable, "retryable_error", "cloud store is unavailable")
		return
	}
	status := http.StatusOK
	if !acquired {
		status = http.StatusConflict
	}
	writeJSON(w, status, BaiduRefreshLeaseResponse{
		Acquired:       acquired,
		AccountID:      lease.AccountID,
		LeaseID:        lease.LeaseID,
		HolderDeviceID: lease.HolderDeviceID,
		ExpiresAt:      lease.ExpiresAt,
	})
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
