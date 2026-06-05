package cloudapi

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/url"
	"strings"
	"time"
)

func validateBaiduEncryptionRequest(method string, rsaPublicKeyPEM string) error {
	switch method {
	case BaiduEncryptionPassword:
		return nil
	case BaiduEncryptionRSA:
		if strings.TrimSpace(rsaPublicKeyPEM) == "" {
			return errors.New("rsa_public_key_pem is required for RSA token encryption")
		}
		_, _, err := parseRSAPublicKey(rsaPublicKeyPEM)
		return err
	default:
		return errors.New("encryption_method must be password_argon2id_aes256gcm_v1 or rsa_oaep_sha256_aes256gcm_v1")
	}
}

func validateEncryptedTokenUpdate(req UpdateBaiduTokenRequest) error {
	if req.TokenExpiresAt.IsZero() {
		return errors.New("token_expires_at is required")
	}
	switch req.EncryptionMethod {
	case BaiduEncryptionPassword, BaiduEncryptionRSA:
	default:
		return errors.New("encryption_method must be password_argon2id_aes256gcm_v1 or rsa_oaep_sha256_aes256gcm_v1")
	}
	if len(req.EncryptedToken) == 0 || !json.Valid(req.EncryptedToken) {
		return errors.New("encrypted_token_json must be valid JSON")
	}
	return nil
}

func (s *Server) buildBaiduAuthorizeURL(state, scope string) string {
	endpoint, err := url.Parse(s.baiduOAuthConfig.AuthorizeURL)
	if err != nil {
		return ""
	}
	query := endpoint.Query()
	query.Set("response_type", "code")
	query.Set("client_id", s.baiduOAuthConfig.AppKey)
	query.Set("redirect_uri", s.baiduOAuthConfig.RedirectURI)
	query.Set("scope", scope)
	query.Set("state", state)
	endpoint.RawQuery = query.Encode()
	return endpoint.String()
}

func (s *Server) exchangeBaiduSessionToken(ctx context.Context, session BaiduAuthSession) (BaiduTokenSet, error) {
	switch session.Flow {
	case BaiduAuthFlowDeviceCode:
		if strings.TrimSpace(session.DeviceCode) == "" {
			return BaiduTokenSet{}, errors.New("device authorization is not ready")
		}
		return s.baiduOAuthClient.ExchangeDeviceCode(ctx, session.DeviceCode)
	case BaiduAuthFlowAuthorizationCode:
		if strings.TrimSpace(session.AuthorizationCode) == "" {
			return BaiduTokenSet{}, errors.New("authorization callback has not been received")
		}
		return s.baiduOAuthClient.ExchangeAuthorizationCode(ctx, session.AuthorizationCode)
	default:
		return BaiduTokenSet{}, errors.New("unsupported baidu authorization flow")
	}
}

func (s *Server) encryptBaiduTokenForSession(token BaiduTokenSet, expiresAt time.Time, session BaiduAuthSession, req CompleteBaiduAuthSessionRequest) (json.RawMessage, string, error) {
	switch session.EncryptionMethod {
	case BaiduEncryptionPassword:
		wrappingKeyText := strings.TrimSpace(req.WrappingKeyBase64)
		if wrappingKeyText == "" {
			return nil, "", errors.New("wrapping_key_base64 is required for password token encryption")
		}
		wrappingKey, err := base64.RawURLEncoding.DecodeString(wrappingKeyText)
		if err != nil {
			wrappingKey, err = base64.StdEncoding.DecodeString(wrappingKeyText)
		}
		if err != nil {
			return nil, "", errors.New("wrapping_key_base64 must be base64 encoded")
		}
		encrypted, err := encryptBaiduTokenWithPasswordKey(token, expiresAt, wrappingKey)
		return encrypted, "", err
	case BaiduEncryptionRSA:
		publicKeyPEM := firstNonEmpty(req.RSAPublicKeyPEM, session.RSAPublicKeyPEM)
		privateKeyHint := firstNonEmpty(req.PrivateKeyHint, session.PrivateKeyHint)
		encrypted, err := encryptBaiduTokenWithRSA(token, expiresAt, publicKeyPEM)
		return encrypted, privateKeyHint, err
	default:
		return nil, "", errors.New("unsupported baidu token encryption method")
	}
}

func toBaiduAuthSessionResponse(session BaiduAuthSession) BaiduAuthSessionResponse {
	return BaiduAuthSessionResponse{
		SessionID:        session.SessionID,
		Flow:             session.Flow,
		Status:           session.Status,
		Scope:            session.Scope,
		EncryptionMethod: session.EncryptionMethod,
		UserCode:         session.UserCode,
		VerificationURL:  session.VerificationURL,
		QRCodeURL:        session.QRCodeURL,
		AuthURL:          session.AuthURL,
		ExpiresAt:        session.ExpiresAt,
		CompletedAt:      session.CompletedAt,
		AccountID:        session.AccountID,
		ErrorCode:        session.ErrorCode,
	}
}

func toBaiduAccountResponse(account BaiduAccount) BaiduAccountResponse {
	return BaiduAccountResponse{
		AccountID:        account.AccountID,
		DisplayName:      account.DisplayName,
		BaiduUID:         account.BaiduUID,
		BaiduUK:          account.BaiduUK,
		Scope:            account.Scope,
		TokenExpiresAt:   account.TokenExpiresAt,
		TokenValid:       time.Now().UTC().Before(account.TokenExpiresAt),
		EncryptionMethod: account.EncryptionMethod,
		PrivateKeyHint:   account.PrivateKeyHint,
		TokenVersion:     account.TokenVersion,
		Selected:         account.Selected,
		LastVerifiedAt:   account.LastVerifiedAt,
		LastVerifyStatus: account.LastVerifyStatus,
	}
}

func toBaiduEncryptedToken(account BaiduAccount) BaiduEncryptedToken {
	return BaiduEncryptedToken{
		AccountID:        account.AccountID,
		EncryptionMethod: account.EncryptionMethod,
		PrivateKeyHint:   account.PrivateKeyHint,
		TokenVersion:     account.TokenVersion,
		TokenExpiresAt:   account.TokenExpiresAt,
		EncryptedToken:   account.EncryptedToken,
	}
}

func ptrTime(value time.Time) *time.Time {
	return &value
}
