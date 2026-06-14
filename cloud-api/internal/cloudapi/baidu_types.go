package cloudapi

import (
	"encoding/json"
	"time"
)

const (
	BaiduAuthFlowDeviceCode        = "device_code"
	BaiduAuthFlowAuthorizationCode = "authorization_code"

	BaiduAuthStatusPending    = "pending"
	BaiduAuthStatusAuthorized = "authorized"
	BaiduAuthStatusCompleted  = "completed"
	BaiduAuthStatusFailed     = "failed"
	BaiduAuthStatusExpired    = "expired"

	BaiduEncryptionPassword = "password_argon2id_aes256gcm_v1"
	BaiduEncryptionRSA      = "rsa_oaep_sha256_aes256gcm_v1"
)

type BaiduOAuthConfig struct {
	PublicBaseURL         string
	AppKey                string
	AppSecret             string
	Scope                 string
	RedirectURI           string
	AuthorizeURL          string
	DeviceCodeURL         string
	TokenURL              string
	UserInfoURL           string
	DeviceVerificationURL string
}

type CreateBaiduAuthSessionRequest struct {
	Flow             string `json:"flow"`
	EncryptionMethod string `json:"encryption_method"`
	RSAPublicKeyPEM  string `json:"rsa_public_key_pem,omitempty"`
	PrivateKeyHint   string `json:"private_key_hint,omitempty"`
}

type BaiduAuthSessionResponse struct {
	SessionID        string     `json:"session_id"`
	Flow             string     `json:"flow"`
	Status           string     `json:"status"`
	Scope            string     `json:"scope"`
	EncryptionMethod string     `json:"encryption_method"`
	UserCode         string     `json:"user_code,omitempty"`
	VerificationURL  string     `json:"verification_url,omitempty"`
	QRCodeURL        string     `json:"qrcode_url,omitempty"`
	AuthURL          string     `json:"auth_url,omitempty"`
	ExpiresAt        time.Time  `json:"expires_at"`
	CompletedAt      *time.Time `json:"completed_at,omitempty"`
	AccountID        string     `json:"account_id,omitempty"`
	ErrorCode        string     `json:"error_code,omitempty"`
}

type CompleteBaiduAuthSessionRequest struct {
	WrappingKeyBase64 string `json:"wrapping_key_base64,omitempty"`
	RSAPublicKeyPEM   string `json:"rsa_public_key_pem,omitempty"`
	PrivateKeyHint    string `json:"private_key_hint,omitempty"`
}

type CompleteBaiduAuthSessionResponse struct {
	Session BaiduAuthSessionResponse `json:"session"`
	Account BaiduAccountResponse     `json:"account"`
	Token   BaiduEncryptedToken      `json:"token"`
}

type BaiduAccountResponse struct {
	AccountID        string     `json:"account_id"`
	DeviceID         string     `json:"device_id,omitempty"`
	DisplayName      string     `json:"display_name"`
	BaiduUID         string     `json:"baidu_uid"`
	BaiduUK          string     `json:"baidu_uk,omitempty"`
	Scope            string     `json:"scope"`
	TokenExpiresAt   time.Time  `json:"token_expires_at"`
	TokenValid       bool       `json:"token_valid"`
	EncryptionMethod string     `json:"encryption_method"`
	PrivateKeyHint   string     `json:"private_key_hint,omitempty"`
	TokenVersion     int64      `json:"token_version"`
	Selected         bool       `json:"selected"`
	CurrentDevice    bool       `json:"current_device"`
	LastVerifiedAt   *time.Time `json:"last_verified_at,omitempty"`
	LastVerifyStatus string     `json:"last_verify_status"`
}

type ListBaiduAccountsResponse struct {
	Accounts []BaiduAccountResponse `json:"accounts"`
}

type BaiduEncryptedToken struct {
	AccountID        string          `json:"account_id"`
	EncryptionMethod string          `json:"encryption_method"`
	PrivateKeyHint   string          `json:"private_key_hint,omitempty"`
	TokenVersion     int64           `json:"token_version"`
	TokenExpiresAt   time.Time       `json:"token_expires_at"`
	EncryptedToken   json.RawMessage `json:"encrypted_token_json"`
}

type UpdateBaiduTokenRequest struct {
	ExpectedTokenVersion int64           `json:"expected_token_version"`
	TokenExpiresAt       time.Time       `json:"token_expires_at"`
	EncryptionMethod     string          `json:"encryption_method"`
	EncryptedToken       json.RawMessage `json:"encrypted_token_json"`
	PrivateKeyHint       string          `json:"private_key_hint,omitempty"`
	LastVerifyStatus     string          `json:"last_verify_status,omitempty"`
}

type BaiduRefreshLeaseRequest struct {
	LeaseID         string `json:"lease_id,omitempty"`
	DurationSeconds int64  `json:"duration_seconds,omitempty"`
}

type BaiduRefreshLeaseResponse struct {
	Acquired       bool      `json:"acquired"`
	AccountID      string    `json:"account_id"`
	LeaseID        string    `json:"lease_id,omitempty"`
	HolderDeviceID string    `json:"holder_device_id,omitempty"`
	ExpiresAt      time.Time `json:"expires_at,omitempty"`
}

type BaiduAuthSession struct {
	SessionID           string
	Flow                string
	Status              string
	RequestedByDeviceID string
	State               string
	Scope               string
	EncryptionMethod    string
	RSAPublicKeyPEM     string
	PrivateKeyHint      string
	DeviceCode          string
	UserCode            string
	VerificationURL     string
	QRCodeURL           string
	AuthURL             string
	AuthorizationCode   string
	ErrorCode           string
	ErrorDescription    string
	ExpiresAt           time.Time
	CompletedAt         *time.Time
	AccountID           string
}

type BaiduAccount struct {
	AccountID        string
	DeviceID         string
	BaiduUID         string
	BaiduUK          string
	DisplayName      string
	Scope            string
	TokenExpiresAt   time.Time
	EncryptionMethod string
	EncryptedToken   json.RawMessage
	PrivateKeyHint   string
	TokenVersion     int64
	LastVerifiedAt   *time.Time
	LastVerifyStatus string
	Selected         bool
	CurrentDevice    bool
}

type BaiduRefreshLease struct {
	AccountID      string
	LeaseID        string
	HolderDeviceID string
	ExpiresAt      time.Time
}

type BaiduDeviceAuthStart struct {
	DeviceCode      string
	UserCode        string
	VerificationURL string
	QRCodeURL       string
	ExpiresIn       int64
	Interval        int64
}

type BaiduTokenSet struct {
	AccessToken  string
	RefreshToken string
	ExpiresIn    int64
	Scope        string
	TokenType    string
}

type BaiduUserInfo struct {
	UID         string
	UK          string
	DisplayName string
}
