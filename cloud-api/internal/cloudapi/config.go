package cloudapi

import (
	"fmt"
	"net/url"
	"os"
)

type Config struct {
	Addr        string
	PostgresDSN string
	BaiduOAuth  BaiduOAuthConfig
}

func LoadConfig() Config {
	addr := getenv("CLOUD_API_ADDR", ":8080")
	dsn := os.Getenv("POSTGRES_DSN")
	if dsn == "" {
		user := getenv("POSTGRES_USER", "auto_backup_user")
		password := os.Getenv("POSTGRES_PASSWORD")
		host := getenv("POSTGRES_HOST", "127.0.0.1")
		port := getenv("POSTGRES_PORT", "5432")
		db := getenv("POSTGRES_DB", "auto_backup_bdnetdesk")
		sslmode := getenv("POSTGRES_SSLMODE", "disable")
		dsn = fmt.Sprintf(
			"postgres://%s:%s@%s:%s/%s?sslmode=%s",
			url.QueryEscape(user),
			url.QueryEscape(password),
			host,
			port,
			url.PathEscape(db),
			url.QueryEscape(sslmode),
		)
	}

	baidu := DefaultBaiduOAuthConfig()
	baidu.PublicBaseURL = getenv("PUBLIC_BASE_URL", baidu.PublicBaseURL)
	baidu.AppKey = os.Getenv("BAIDU_APP_KEY")
	baidu.AppSecret = os.Getenv("BAIDU_APP_SECRET")
	baidu.Scope = getenv("BAIDU_SCOPE", baidu.Scope)
	baidu.RedirectURI = getenv("BAIDU_REDIRECT_URI", baidu.RedirectURI)
	baidu.AuthorizeURL = getenv("BAIDU_AUTHORIZE_URL", baidu.AuthorizeURL)
	baidu.DeviceCodeURL = getenv("BAIDU_DEVICE_CODE_URL", baidu.DeviceCodeURL)
	baidu.TokenURL = getenv("BAIDU_TOKEN_URL", baidu.TokenURL)
	baidu.UserInfoURL = getenv("BAIDU_USERINFO_URL", baidu.UserInfoURL)
	baidu.DeviceVerificationURL = getenv("BAIDU_DEVICE_VERIFICATION_URL", baidu.DeviceVerificationURL)

	return Config{
		Addr:        addr,
		PostgresDSN: dsn,
		BaiduOAuth:  baidu,
	}
}

func DefaultBaiduOAuthConfig() BaiduOAuthConfig {
	publicBaseURL := "http://127.0.0.1:8080"
	return BaiduOAuthConfig{
		PublicBaseURL:         publicBaseURL,
		Scope:                 "basic,netdisk",
		RedirectURI:           publicBaseURL + "/v1/baidu/oauth/callback",
		AuthorizeURL:          "https://openapi.baidu.com/oauth/2.0/authorize",
		DeviceCodeURL:         "https://openapi.baidu.com/oauth/2.0/device/code",
		TokenURL:              "https://openapi.baidu.com/oauth/2.0/token",
		UserInfoURL:           "https://pan.baidu.com/rest/2.0/xpan/nas?method=uinfo",
		DeviceVerificationURL: "https://openapi.baidu.com/device",
	}
}

func getenv(key, fallback string) string {
	value := os.Getenv(key)
	if value == "" {
		return fallback
	}
	return value
}
