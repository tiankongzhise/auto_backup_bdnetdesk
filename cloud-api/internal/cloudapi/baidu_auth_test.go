package cloudapi

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

type fakeBaiduOAuthClient struct {
	deviceCode        string
	authorizationCode string
	accessToken       string
	refreshToken      string
	userInfo          BaiduUserInfo
}

func newFakeBaiduOAuthClient() *fakeBaiduOAuthClient {
	return &fakeBaiduOAuthClient{
		deviceCode:        "fake-device-code-secret",
		authorizationCode: "fake-auth-code-secret",
		accessToken:       "fake-access-token-secret",
		refreshToken:      "fake-refresh-token-secret",
		userInfo: BaiduUserInfo{
			UID:         "baidu-uid-1",
			UK:          "baidu-uk-1",
			DisplayName: "测试百度账号",
		},
	}
}

func (c *fakeBaiduOAuthClient) StartDeviceAuth(_ context.Context, scope string) (BaiduDeviceAuthStart, error) {
	return BaiduDeviceAuthStart{
		DeviceCode:      c.deviceCode,
		UserCode:        "ABCD-EFGH",
		VerificationURL: "https://openapi.baidu.com/device",
		QRCodeURL:       "https://openapi.baidu.com/device/qrcode/fake",
		ExpiresIn:       600,
		Interval:        5,
	}, nil
}

func (c *fakeBaiduOAuthClient) ExchangeDeviceCode(_ context.Context, deviceCode string) (BaiduTokenSet, error) {
	if deviceCode != c.deviceCode {
		return BaiduTokenSet{}, context.Canceled
	}
	return c.tokenSet(), nil
}

func (c *fakeBaiduOAuthClient) ExchangeAuthorizationCode(_ context.Context, code string) (BaiduTokenSet, error) {
	if code != c.authorizationCode {
		return BaiduTokenSet{}, context.Canceled
	}
	return c.tokenSet(), nil
}

func (c *fakeBaiduOAuthClient) GetUserInfo(_ context.Context, accessToken string) (BaiduUserInfo, error) {
	if accessToken != c.accessToken {
		return BaiduUserInfo{}, context.Canceled
	}
	return c.userInfo, nil
}

func (c *fakeBaiduOAuthClient) tokenSet() BaiduTokenSet {
	return BaiduTokenSet{
		AccessToken:  c.accessToken,
		RefreshToken: c.refreshToken,
		ExpiresIn:    3600,
		Scope:        "basic,netdisk",
		TokenType:    "Bearer",
	}
}

func TestBaiduDeviceAuthPasswordFlow(t *testing.T) {
	store := newMemoryStore()
	fakeOAuth := newFakeBaiduOAuthClient()
	logBuffer := bytes.NewBuffer(nil)
	handler := NewServer(
		store,
		slog.New(slog.NewTextHandler(logBuffer, nil)),
		WithBaiduOAuthConfig(testBaiduOAuthConfig()),
		WithBaiduOAuthClient(fakeOAuth),
	)
	token := registerDevice(t, handler).DeviceToken

	createBody := `{"flow":"device_code","encryption_method":"password_argon2id_aes256gcm_v1"}`
	createRec := authedJSON(t, handler, http.MethodPost, "/v1/baidu/auth/sessions", token, createBody)
	if createRec.Code != http.StatusCreated {
		t.Fatalf("expected create session 201, got %d: %s", createRec.Code, createRec.Body.String())
	}
	assertNotContainsSensitive(t, createRec.Body.String(), fakeOAuth.deviceCode, fakeOAuth.accessToken, fakeOAuth.refreshToken)

	var session BaiduAuthSessionResponse
	decodeResponse(t, createRec, &session)
	if session.SessionID == "" || session.UserCode != "ABCD-EFGH" {
		t.Fatalf("unexpected session response: %#v", session)
	}
	if session.EncryptionMethod != BaiduEncryptionPassword {
		t.Fatalf("expected password encryption method, got %s", session.EncryptionMethod)
	}

	wrappingKey := bytes.Repeat([]byte{7}, 32)
	wrappingKeyText := base64.RawURLEncoding.EncodeToString(wrappingKey)
	completeBody := `{"wrapping_key_base64":"` + wrappingKeyText + `"}`
	completeRec := authedJSON(t, handler, http.MethodPost, "/v1/baidu/auth/sessions/"+session.SessionID+"/complete", token, completeBody)
	if completeRec.Code != http.StatusOK {
		t.Fatalf("expected complete session 200, got %d: %s", completeRec.Code, completeRec.Body.String())
	}
	assertNotContainsSensitive(t, completeRec.Body.String(), wrappingKeyText, fakeOAuth.deviceCode, fakeOAuth.accessToken, fakeOAuth.refreshToken)
	assertNotContainsSensitive(t, logBuffer.String(), wrappingKeyText, fakeOAuth.deviceCode, fakeOAuth.accessToken, fakeOAuth.refreshToken)

	var complete CompleteBaiduAuthSessionResponse
	decodeResponse(t, completeRec, &complete)
	if complete.Session.Status != BaiduAuthStatusCompleted {
		t.Fatalf("expected completed session, got %#v", complete.Session)
	}
	if complete.Account.DisplayName != "测试百度账号" || !complete.Account.Selected {
		t.Fatalf("unexpected account response: %#v", complete.Account)
	}
	if complete.Token.EncryptionMethod != BaiduEncryptionPassword {
		t.Fatalf("expected password token envelope, got %#v", complete.Token)
	}
	if !bytes.Contains(complete.Token.EncryptedToken, []byte(`"ciphertext"`)) {
		t.Fatalf("expected encrypted token envelope, got %s", string(complete.Token.EncryptedToken))
	}

	listRec := authedJSON(t, handler, http.MethodGet, "/v1/baidu/accounts", token, "")
	if listRec.Code != http.StatusOK {
		t.Fatalf("expected list accounts 200, got %d: %s", listRec.Code, listRec.Body.String())
	}
	var list ListBaiduAccountsResponse
	decodeResponse(t, listRec, &list)
	if len(list.Accounts) != 1 || !list.Accounts[0].Selected {
		t.Fatalf("expected selected account in list, got %#v", list)
	}

	tokenRec := authedJSON(t, handler, http.MethodGet, "/v1/baidu/accounts/"+complete.Account.AccountID+"/token", token, "")
	if tokenRec.Code != http.StatusOK {
		t.Fatalf("expected token response 200, got %d: %s", tokenRec.Code, tokenRec.Body.String())
	}
	assertNotContainsSensitive(t, tokenRec.Body.String(), fakeOAuth.accessToken, fakeOAuth.refreshToken)
}

func TestBaiduAuthorizationCodeCallbackAndRSAFlow(t *testing.T) {
	store := newMemoryStore()
	fakeOAuth := newFakeBaiduOAuthClient()
	handler := NewServer(
		store,
		slog.New(slog.NewTextHandler(bytes.NewBuffer(nil), nil)),
		WithBaiduOAuthConfig(testBaiduOAuthConfig()),
		WithBaiduOAuthClient(fakeOAuth),
	)
	token := registerDevice(t, handler).DeviceToken
	publicKeyPEM := testRSAPublicKeyPEM(t)

	createPayload, err := json.Marshal(CreateBaiduAuthSessionRequest{
		Flow:             BaiduAuthFlowAuthorizationCode,
		EncryptionMethod: BaiduEncryptionRSA,
		RSAPublicKeyPEM:  publicKeyPEM,
		PrivateKeyHint:   "C:/secure/baidu-token-private.pem",
	})
	if err != nil {
		t.Fatalf("marshal create payload: %v", err)
	}
	createRec := authedJSON(t, handler, http.MethodPost, "/v1/baidu/auth/sessions", token, string(createPayload))
	if createRec.Code != http.StatusCreated {
		t.Fatalf("expected create auth code session 201, got %d: %s", createRec.Code, createRec.Body.String())
	}
	var session BaiduAuthSessionResponse
	decodeResponse(t, createRec, &session)
	if !strings.Contains(session.AuthURL, "backup.baichengedu.com") || !strings.Contains(session.AuthURL, "state=") {
		t.Fatalf("expected auth url with callback domain and state, got %s", session.AuthURL)
	}
	if strings.Contains(createRec.Body.String(), fakeOAuth.authorizationCode) {
		t.Fatalf("create response leaked authorization code: %s", createRec.Body.String())
	}

	storedSession, ok, err := store.GetBaiduAuthSession(context.Background(), session.SessionID)
	if err != nil || !ok {
		t.Fatalf("expected stored session, ok=%v err=%v", ok, err)
	}
	callbackRec := httptest.NewRecorder()
	callbackReq := httptest.NewRequest(http.MethodGet, "/v1/baidu/oauth/callback?state="+storedSession.State+"&code="+fakeOAuth.authorizationCode, nil)
	handler.ServeHTTP(callbackRec, callbackReq)
	if callbackRec.Code != http.StatusOK {
		t.Fatalf("expected callback 200, got %d: %s", callbackRec.Code, callbackRec.Body.String())
	}
	if strings.Contains(callbackRec.Body.String(), fakeOAuth.authorizationCode) {
		t.Fatalf("callback response leaked authorization code: %s", callbackRec.Body.String())
	}

	completeRec := authedJSON(t, handler, http.MethodPost, "/v1/baidu/auth/sessions/"+session.SessionID+"/complete", token, `{}`)
	if completeRec.Code != http.StatusOK {
		t.Fatalf("expected complete RSA session 200, got %d: %s", completeRec.Code, completeRec.Body.String())
	}
	var complete CompleteBaiduAuthSessionResponse
	decodeResponse(t, completeRec, &complete)
	if complete.Token.EncryptionMethod != BaiduEncryptionRSA {
		t.Fatalf("expected RSA encrypted token, got %#v", complete.Token)
	}
	if complete.Token.PrivateKeyHint != "C:/secure/baidu-token-private.pem" {
		t.Fatalf("expected private key hint, got %#v", complete.Token)
	}
	if !bytes.Contains(complete.Token.EncryptedToken, []byte(`"wrapped_key"`)) {
		t.Fatalf("expected RSA wrapped key in envelope, got %s", string(complete.Token.EncryptedToken))
	}
	assertNotContainsSensitive(t, completeRec.Body.String(), fakeOAuth.authorizationCode, fakeOAuth.accessToken, fakeOAuth.refreshToken)
}

func TestBaiduAccountSelectionAndRefreshLease(t *testing.T) {
	store := newMemoryStore()
	handler := NewServer(
		store,
		slog.New(slog.NewTextHandler(bytes.NewBuffer(nil), nil)),
		WithBaiduOAuthConfig(testBaiduOAuthConfig()),
		WithBaiduOAuthClient(newFakeBaiduOAuthClient()),
	)
	firstToken := registerDevice(t, handler).DeviceToken
	secondToken := registerDevice(t, handler).DeviceToken

	accountID := createPasswordBaiduAccount(t, handler, firstToken)

	secondList := authedJSON(t, handler, http.MethodGet, "/v1/baidu/accounts", secondToken, "")
	if secondList.Code != http.StatusOK {
		t.Fatalf("expected second device account list 200, got %d: %s", secondList.Code, secondList.Body.String())
	}
	var list ListBaiduAccountsResponse
	decodeResponse(t, secondList, &list)
	if len(list.Accounts) != 1 || list.Accounts[0].Selected {
		t.Fatalf("expected visible but unselected shared account, got %#v", list)
	}

	selectRec := authedJSON(t, handler, http.MethodPost, "/v1/baidu/accounts/"+accountID+"/select", secondToken, `{}`)
	if selectRec.Code != http.StatusOK {
		t.Fatalf("expected select 200, got %d: %s", selectRec.Code, selectRec.Body.String())
	}
	var selected BaiduAccountResponse
	decodeResponse(t, selectRec, &selected)
	if !selected.Selected {
		t.Fatalf("expected second device selected account, got %#v", selected)
	}

	secondTokenRec := authedJSON(t, handler, http.MethodGet, "/v1/baidu/accounts/"+accountID+"/token", secondToken, "")
	if secondTokenRec.Code != http.StatusNotFound {
		t.Fatalf("expected selected second device without authorization to have no token, got %d: %s", secondTokenRec.Code, secondTokenRec.Body.String())
	}

	secondLeaseWithoutAuth := authedJSON(t, handler, http.MethodPost, "/v1/baidu/accounts/"+accountID+"/refresh-lease", secondToken, `{"lease_id":"lease-second","duration_seconds":300}`)
	if secondLeaseWithoutAuth.Code != http.StatusNotFound {
		t.Fatalf("expected selected second device without authorization to have no refresh lease, got %d: %s", secondLeaseWithoutAuth.Code, secondLeaseWithoutAuth.Body.String())
	}

	leaseRec := authedJSON(t, handler, http.MethodPost, "/v1/baidu/accounts/"+accountID+"/refresh-lease", firstToken, `{"lease_id":"lease-first","duration_seconds":300}`)
	if leaseRec.Code != http.StatusOK {
		t.Fatalf("expected first lease 200, got %d: %s", leaseRec.Code, leaseRec.Body.String())
	}
	var firstLease BaiduRefreshLeaseResponse
	decodeResponse(t, leaseRec, &firstLease)
	if !firstLease.Acquired || firstLease.LeaseID != "lease-first" {
		t.Fatalf("expected acquired first lease, got %#v", firstLease)
	}
}

func TestBaiduSameUIDAuthorizationIsDeviceScoped(t *testing.T) {
	store := newMemoryStore()
	handler := NewServer(
		store,
		slog.New(slog.NewTextHandler(bytes.NewBuffer(nil), nil)),
		WithBaiduOAuthConfig(testBaiduOAuthConfig()),
		WithBaiduOAuthClient(newFakeBaiduOAuthClient()),
	)
	firstToken := registerDevice(t, handler).DeviceToken
	secondToken := registerDevice(t, handler).DeviceToken

	firstAccountID := createPasswordBaiduAccountWithKey(t, handler, firstToken, bytes.Repeat([]byte{3}, 32))
	firstTokenRec := authedJSON(t, handler, http.MethodGet, "/v1/baidu/accounts/"+firstAccountID+"/token", firstToken, "")
	if firstTokenRec.Code != http.StatusOK {
		t.Fatalf("expected first device token 200, got %d: %s", firstTokenRec.Code, firstTokenRec.Body.String())
	}
	var firstBefore BaiduEncryptedToken
	decodeResponse(t, firstTokenRec, &firstBefore)
	if firstBefore.TokenVersion != 1 {
		t.Fatalf("expected first device token version 1, got %#v", firstBefore)
	}

	secondAccountID := createPasswordBaiduAccountWithKey(t, handler, secondToken, bytes.Repeat([]byte{4}, 32))
	if secondAccountID != firstAccountID {
		t.Fatalf("expected same baidu UID to reuse account identity, first=%s second=%s", firstAccountID, secondAccountID)
	}
	secondTokenRec := authedJSON(t, handler, http.MethodGet, "/v1/baidu/accounts/"+firstAccountID+"/token", secondToken, "")
	if secondTokenRec.Code != http.StatusOK {
		t.Fatalf("expected second device token 200, got %d: %s", secondTokenRec.Code, secondTokenRec.Body.String())
	}
	var secondBefore BaiduEncryptedToken
	decodeResponse(t, secondTokenRec, &secondBefore)
	if secondBefore.TokenVersion != 1 {
		t.Fatalf("expected second device token version 1, got %#v", secondBefore)
	}
	if bytes.Equal(firstBefore.EncryptedToken, secondBefore.EncryptedToken) {
		t.Fatalf("expected device-specific encrypted token envelopes to differ")
	}

	firstAfterRec := authedJSON(t, handler, http.MethodGet, "/v1/baidu/accounts/"+firstAccountID+"/token", firstToken, "")
	if firstAfterRec.Code != http.StatusOK {
		t.Fatalf("expected first device token after second auth 200, got %d: %s", firstAfterRec.Code, firstAfterRec.Body.String())
	}
	var firstAfter BaiduEncryptedToken
	decodeResponse(t, firstAfterRec, &firstAfter)
	if firstAfter.TokenVersion != 1 || !bytes.Equal(firstBefore.EncryptedToken, firstAfter.EncryptedToken) {
		t.Fatalf("expected first device token to remain unchanged, before=%#v after=%#v", firstBefore, firstAfter)
	}

	update := UpdateBaiduTokenRequest{
		ExpectedTokenVersion: 1,
		TokenExpiresAt:       time.Now().UTC().Add(2 * time.Hour),
		EncryptionMethod:     BaiduEncryptionPassword,
		EncryptedToken:       json.RawMessage(`{"version":1,"encryption_method":"password_argon2id_aes256gcm_v1","ciphertext":"second-updated"}`),
		LastVerifyStatus:     "valid",
	}
	payload, err := json.Marshal(update)
	if err != nil {
		t.Fatalf("marshal update: %v", err)
	}
	secondUpdate := authedJSON(t, handler, http.MethodPut, "/v1/baidu/accounts/"+firstAccountID+"/token", secondToken, string(payload))
	if secondUpdate.Code != http.StatusOK {
		t.Fatalf("expected second token update 200, got %d: %s", secondUpdate.Code, secondUpdate.Body.String())
	}
	var secondAfter BaiduEncryptedToken
	decodeResponse(t, secondUpdate, &secondAfter)
	if secondAfter.TokenVersion != 2 {
		t.Fatalf("expected second token version 2, got %#v", secondAfter)
	}

	firstFinalRec := authedJSON(t, handler, http.MethodGet, "/v1/baidu/accounts/"+firstAccountID+"/token", firstToken, "")
	if firstFinalRec.Code != http.StatusOK {
		t.Fatalf("expected first token final 200, got %d: %s", firstFinalRec.Code, firstFinalRec.Body.String())
	}
	var firstFinal BaiduEncryptedToken
	decodeResponse(t, firstFinalRec, &firstFinal)
	if firstFinal.TokenVersion != 1 || !bytes.Equal(firstBefore.EncryptedToken, firstFinal.EncryptedToken) {
		t.Fatalf("expected second device update not to affect first device, before=%#v final=%#v", firstBefore, firstFinal)
	}

	firstLeaseRec := authedJSON(t, handler, http.MethodPost, "/v1/baidu/accounts/"+firstAccountID+"/refresh-lease", firstToken, `{"lease_id":"lease-first","duration_seconds":300}`)
	if firstLeaseRec.Code != http.StatusOK {
		t.Fatalf("expected first device refresh lease 200, got %d: %s", firstLeaseRec.Code, firstLeaseRec.Body.String())
	}
	secondLeaseRec := authedJSON(t, handler, http.MethodPost, "/v1/baidu/accounts/"+firstAccountID+"/refresh-lease", secondToken, `{"lease_id":"lease-second","duration_seconds":300}`)
	if secondLeaseRec.Code != http.StatusOK {
		t.Fatalf("expected second device refresh lease 200, got %d: %s", secondLeaseRec.Code, secondLeaseRec.Body.String())
	}
}

func TestBaiduTokenUpdateVersionConflict(t *testing.T) {
	store := newMemoryStore()
	handler := NewServer(
		store,
		slog.New(slog.NewTextHandler(bytes.NewBuffer(nil), nil)),
		WithBaiduOAuthConfig(testBaiduOAuthConfig()),
		WithBaiduOAuthClient(newFakeBaiduOAuthClient()),
	)
	token := registerDevice(t, handler).DeviceToken
	accountID := createPasswordBaiduAccount(t, handler, token)

	update := UpdateBaiduTokenRequest{
		ExpectedTokenVersion: 1,
		TokenExpiresAt:       time.Now().UTC().Add(2 * time.Hour),
		EncryptionMethod:     BaiduEncryptionPassword,
		EncryptedToken:       json.RawMessage(`{"version":1,"encryption_method":"password_argon2id_aes256gcm_v1","ciphertext":"updated"}`),
		LastVerifyStatus:     "valid",
	}
	payload, err := json.Marshal(update)
	if err != nil {
		t.Fatalf("marshal update: %v", err)
	}
	firstUpdate := authedJSON(t, handler, http.MethodPut, "/v1/baidu/accounts/"+accountID+"/token", token, string(payload))
	if firstUpdate.Code != http.StatusOK {
		t.Fatalf("expected first token update 200, got %d: %s", firstUpdate.Code, firstUpdate.Body.String())
	}

	secondUpdate := authedJSON(t, handler, http.MethodPut, "/v1/baidu/accounts/"+accountID+"/token", token, string(payload))
	if secondUpdate.Code != http.StatusConflict {
		t.Fatalf("expected stale token update 409, got %d: %s", secondUpdate.Code, secondUpdate.Body.String())
	}
}

func createPasswordBaiduAccount(t *testing.T, handler http.Handler, token string) string {
	t.Helper()

	return createPasswordBaiduAccountWithKey(t, handler, token, bytes.Repeat([]byte{3}, 32))
}

func createPasswordBaiduAccountWithKey(t *testing.T, handler http.Handler, token string, wrappingKey []byte) string {
	t.Helper()

	createRec := authedJSON(t, handler, http.MethodPost, "/v1/baidu/auth/sessions", token, `{"flow":"device_code","encryption_method":"password_argon2id_aes256gcm_v1"}`)
	if createRec.Code != http.StatusCreated {
		t.Fatalf("create session failed: %d %s", createRec.Code, createRec.Body.String())
	}
	var session BaiduAuthSessionResponse
	decodeResponse(t, createRec, &session)

	wrappingKeyText := base64.RawURLEncoding.EncodeToString(wrappingKey)
	completeRec := authedJSON(t, handler, http.MethodPost, "/v1/baidu/auth/sessions/"+session.SessionID+"/complete", token, `{"wrapping_key_base64":"`+wrappingKeyText+`"}`)
	if completeRec.Code != http.StatusOK {
		t.Fatalf("complete session failed: %d %s", completeRec.Code, completeRec.Body.String())
	}
	var complete CompleteBaiduAuthSessionResponse
	decodeResponse(t, completeRec, &complete)
	return complete.Account.AccountID
}

func authedJSON(t *testing.T, handler http.Handler, method string, path string, token string, body string) *httptest.ResponseRecorder {
	t.Helper()

	var reader *bytes.Reader
	if body == "" {
		reader = bytes.NewReader(nil)
	} else {
		reader = bytes.NewReader([]byte(body))
	}
	req := httptest.NewRequest(method, path, reader)
	req.Header.Set("Authorization", "Bearer "+token)
	if body != "" {
		req.Header.Set("Content-Type", "application/json")
	}
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	return rec
}

func decodeResponse(t *testing.T, rec *httptest.ResponseRecorder, out any) {
	t.Helper()
	if err := json.NewDecoder(rec.Body).Decode(out); err != nil {
		t.Fatalf("decode response: %v; body=%s", err, rec.Body.String())
	}
}

func assertNotContainsSensitive(t *testing.T, body string, values ...string) {
	t.Helper()
	for _, value := range values {
		if value == "" {
			continue
		}
		if strings.Contains(body, value) {
			t.Fatalf("response leaked sensitive value %q in %s", value, body)
		}
	}
}

func testBaiduOAuthConfig() BaiduOAuthConfig {
	cfg := DefaultBaiduOAuthConfig()
	cfg.PublicBaseURL = "https://backup.baichengedu.com"
	cfg.AppKey = "fake-baidu-app-key"
	cfg.AppSecret = "fake-baidu-app-secret"
	cfg.Scope = "basic,netdisk"
	cfg.RedirectURI = "https://backup.baichengedu.com/v1/baidu/oauth/callback"
	return cfg
}

func testRSAPublicKeyPEM(t *testing.T) string {
	t.Helper()

	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatalf("generate rsa key: %v", err)
	}
	der, err := x509.MarshalPKIXPublicKey(&key.PublicKey)
	if err != nil {
		t.Fatalf("marshal public key: %v", err)
	}
	return string(pem.EncodeToMemory(&pem.Block{
		Type:  "PUBLIC KEY",
		Bytes: der,
	}))
}
