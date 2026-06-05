package cloudapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

type BaiduOAuthClient interface {
	StartDeviceAuth(ctx context.Context, scope string) (BaiduDeviceAuthStart, error)
	ExchangeDeviceCode(ctx context.Context, deviceCode string) (BaiduTokenSet, error)
	ExchangeAuthorizationCode(ctx context.Context, code string) (BaiduTokenSet, error)
	GetUserInfo(ctx context.Context, accessToken string) (BaiduUserInfo, error)
}

type HTTPBaiduOAuthClient struct {
	cfg        BaiduOAuthConfig
	httpClient *http.Client
}

func NewHTTPBaiduOAuthClient(cfg BaiduOAuthConfig) *HTTPBaiduOAuthClient {
	return &HTTPBaiduOAuthClient{
		cfg: cfg,
		httpClient: &http.Client{
			Timeout: 15 * time.Second,
		},
	}
}

func (c *HTTPBaiduOAuthClient) StartDeviceAuth(ctx context.Context, scope string) (BaiduDeviceAuthStart, error) {
	if err := c.requireAppKey(); err != nil {
		return BaiduDeviceAuthStart{}, err
	}
	values := url.Values{}
	values.Set("response_type", "device_code")
	values.Set("client_id", c.cfg.AppKey)
	values.Set("scope", scope)

	var resp struct {
		DeviceCode      string `json:"device_code"`
		UserCode        string `json:"user_code"`
		VerificationURL string `json:"verification_url"`
		VerificationURI string `json:"verification_uri"`
		QRCodeURL       string `json:"qrcode_url"`
		ExpiresIn       int64  `json:"expires_in"`
		Interval        int64  `json:"interval"`
	}
	if err := c.doForm(ctx, c.cfg.DeviceCodeURL, values, &resp); err != nil {
		return BaiduDeviceAuthStart{}, err
	}
	verificationURL := firstNonEmpty(resp.VerificationURL, resp.VerificationURI, c.cfg.DeviceVerificationURL)
	if resp.DeviceCode == "" || resp.UserCode == "" {
		return BaiduDeviceAuthStart{}, errors.New("baidu device auth response is incomplete")
	}
	if resp.ExpiresIn <= 0 {
		resp.ExpiresIn = 1800
	}
	return BaiduDeviceAuthStart{
		DeviceCode:      resp.DeviceCode,
		UserCode:        resp.UserCode,
		VerificationURL: verificationURL,
		QRCodeURL:       resp.QRCodeURL,
		ExpiresIn:       resp.ExpiresIn,
		Interval:        resp.Interval,
	}, nil
}

func (c *HTTPBaiduOAuthClient) ExchangeDeviceCode(ctx context.Context, deviceCode string) (BaiduTokenSet, error) {
	if err := c.requireAppSecret(); err != nil {
		return BaiduTokenSet{}, err
	}
	values := url.Values{}
	values.Set("grant_type", "device_token")
	values.Set("code", deviceCode)
	values.Set("client_id", c.cfg.AppKey)
	values.Set("client_secret", c.cfg.AppSecret)
	return c.exchangeToken(ctx, values)
}

func (c *HTTPBaiduOAuthClient) ExchangeAuthorizationCode(ctx context.Context, code string) (BaiduTokenSet, error) {
	if err := c.requireAppSecret(); err != nil {
		return BaiduTokenSet{}, err
	}
	values := url.Values{}
	values.Set("grant_type", "authorization_code")
	values.Set("code", code)
	values.Set("client_id", c.cfg.AppKey)
	values.Set("client_secret", c.cfg.AppSecret)
	values.Set("redirect_uri", c.cfg.RedirectURI)
	return c.exchangeToken(ctx, values)
}

func (c *HTTPBaiduOAuthClient) GetUserInfo(ctx context.Context, accessToken string) (BaiduUserInfo, error) {
	if strings.TrimSpace(accessToken) == "" {
		return BaiduUserInfo{}, errors.New("baidu access token is required")
	}
	endpoint, err := url.Parse(c.cfg.UserInfoURL)
	if err != nil {
		return BaiduUserInfo{}, errors.New("invalid baidu userinfo url")
	}
	query := endpoint.Query()
	query.Set("access_token", accessToken)
	endpoint.RawQuery = query.Encode()

	var resp struct {
		UID         json.RawMessage `json:"uid"`
		UK          json.RawMessage `json:"uk"`
		BaiduName   string          `json:"baidu_name"`
		NetdiskName string          `json:"netdisk_name"`
		AvatarURL   string          `json:"avatar_url"`
	}
	if err := c.doGET(ctx, endpoint.String(), &resp); err != nil {
		return BaiduUserInfo{}, err
	}
	uid := rawJSONScalar(resp.UID)
	uk := rawJSONScalar(resp.UK)
	if uid == "" {
		uid = uk
	}
	if uid == "" {
		return BaiduUserInfo{}, errors.New("baidu userinfo response is incomplete")
	}
	return BaiduUserInfo{
		UID:         uid,
		UK:          uk,
		DisplayName: firstNonEmpty(resp.NetdiskName, resp.BaiduName, uid),
	}, nil
}

func (c *HTTPBaiduOAuthClient) exchangeToken(ctx context.Context, values url.Values) (BaiduTokenSet, error) {
	var resp struct {
		AccessToken  string          `json:"access_token"`
		RefreshToken string          `json:"refresh_token"`
		ExpiresIn    json.RawMessage `json:"expires_in"`
		Scope        string          `json:"scope"`
		TokenType    string          `json:"token_type"`
	}
	if err := c.doForm(ctx, c.cfg.TokenURL, values, &resp); err != nil {
		return BaiduTokenSet{}, err
	}
	if resp.AccessToken == "" || resp.RefreshToken == "" {
		return BaiduTokenSet{}, errors.New("baidu token response is incomplete")
	}
	expiresIn := int64(0)
	if len(resp.ExpiresIn) > 0 {
		expiresIn = parseJSONInt64(resp.ExpiresIn)
	}
	if expiresIn <= 0 {
		expiresIn = 30 * 24 * 60 * 60
	}
	return BaiduTokenSet{
		AccessToken:  resp.AccessToken,
		RefreshToken: resp.RefreshToken,
		ExpiresIn:    expiresIn,
		Scope:        resp.Scope,
		TokenType:    firstNonEmpty(resp.TokenType, "Bearer"),
	}, nil
}

func (c *HTTPBaiduOAuthClient) doForm(ctx context.Context, endpoint string, values url.Values, out any) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, strings.NewReader(values.Encode()))
	if err != nil {
		return errors.New("failed to create baidu request")
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	return c.do(req, out)
}

func (c *HTTPBaiduOAuthClient) doGET(ctx context.Context, endpoint string, out any) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return errors.New("failed to create baidu request")
	}
	return c.do(req, out)
}

func (c *HTTPBaiduOAuthClient) do(req *http.Request, out any) error {
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return errors.New("baidu oauth request failed")
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		return errors.New("failed to read baidu oauth response")
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("baidu oauth returned status %d", resp.StatusCode)
	}

	var oauthErr struct {
		Error            string `json:"error"`
		ErrorDescription string `json:"error_description"`
	}
	if err := json.Unmarshal(body, &oauthErr); err == nil && oauthErr.Error != "" {
		return fmt.Errorf("baidu oauth error: %s", oauthErr.Error)
	}
	if err := json.Unmarshal(body, out); err != nil {
		return errors.New("failed to decode baidu oauth response")
	}
	return nil
}

func (c *HTTPBaiduOAuthClient) requireAppKey() error {
	if strings.TrimSpace(c.cfg.AppKey) == "" {
		return errors.New("baidu app key is not configured")
	}
	return nil
}

func (c *HTTPBaiduOAuthClient) requireAppSecret() error {
	if err := c.requireAppKey(); err != nil {
		return err
	}
	if strings.TrimSpace(c.cfg.AppSecret) == "" {
		return errors.New("baidu app secret is not configured")
	}
	return nil
}

func parseJSONInt64(raw json.RawMessage) int64 {
	var n int64
	if err := json.Unmarshal(raw, &n); err == nil {
		return n
	}
	var f float64
	if err := json.Unmarshal(raw, &f); err == nil {
		return int64(f)
	}
	var s string
	if err := json.Unmarshal(raw, &s); err == nil {
		n, _ := strconv.ParseInt(s, 10, 64)
		return n
	}
	return 0
}

func rawJSONScalar(raw json.RawMessage) string {
	if len(raw) == 0 {
		return ""
	}
	var s string
	if err := json.Unmarshal(raw, &s); err == nil {
		return s
	}
	var n int64
	if err := json.Unmarshal(raw, &n); err == nil {
		return strconv.FormatInt(n, 10)
	}
	var f float64
	if err := json.Unmarshal(raw, &f); err == nil {
		return strconv.FormatInt(int64(f), 10)
	}
	return ""
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			return value
		}
	}
	return ""
}
