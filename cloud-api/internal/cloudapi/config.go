package cloudapi

import (
	"bufio"
	"fmt"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
)

const EnvFileVariable = "CLOUD_API_ENV_FILE"

type ConfigOptions struct {
	EnvFile string
}

type Config struct {
	AppEnv      string
	LogLevel    string
	Addr        string
	Listen      ListenAddressInfo
	PostgresDSN string
	Postgres    PostgresConnectionInfo
	EnvFiles    EnvFileReport
	BaiduOAuth  BaiduOAuthConfig
}

type EnvFileReport struct {
	Mode      string
	Requested string
	Loaded    []LoadedEnvFile
	Missing   []string
}

type LoadedEnvFile struct {
	Path           string
	SetKeys        []string
	PreservedKeys  []string
	IgnoredLineCnt int
}

type PostgresConnectionInfo struct {
	Source          string
	DSNFormat       string
	Host            string
	Port            string
	Database        string
	User            string
	SSLMode         string
	PasswordSet     bool
	DefaultedFields []string
}

type ListenAddressInfo struct {
	Raw                string
	Normalized         string
	BarePortNormalized bool
}

func LoadConfig(options ConfigOptions) (Config, error) {
	envReport, err := loadEnvFiles(options.EnvFile)
	if err != nil {
		return Config{}, err
	}

	addr, listen := resolveListenAddress()
	dsn, postgres := resolvePostgresConfig()
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
		AppEnv:      getenv("APP_ENV", "development"),
		LogLevel:    getenv("LOG_LEVEL", "INFO"),
		Addr:        addr,
		Listen:      listen,
		PostgresDSN: dsn,
		Postgres:    postgres,
		EnvFiles:    envReport,
		BaiduOAuth:  baidu,
	}, nil
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

func (report EnvFileReport) LogAttrs() []any {
	return []any{
		"env_file_mode", report.Mode,
		"env_file_requested", report.Requested,
		"env_files_loaded", report.LoadedPaths(),
		"env_vars_loaded_count", report.SetKeyCount(),
		"env_vars_preserved_count", report.PreservedKeyCount(),
	}
}

func (report EnvFileReport) LoadedPaths() []string {
	paths := make([]string, 0, len(report.Loaded))
	for _, loaded := range report.Loaded {
		paths = append(paths, loaded.Path)
	}
	return paths
}

func (report EnvFileReport) SetKeyCount() int {
	count := 0
	for _, loaded := range report.Loaded {
		count += len(loaded.SetKeys)
	}
	return count
}

func (report EnvFileReport) PreservedKeyCount() int {
	count := 0
	for _, loaded := range report.Loaded {
		count += len(loaded.PreservedKeys)
	}
	return count
}

func (info PostgresConnectionInfo) LogAttrs() []any {
	return []any{
		"postgres_config_source", info.Source,
		"postgres_dsn_format", info.DSNFormat,
		"postgres_host", info.Host,
		"postgres_port", info.Port,
		"postgres_database", info.Database,
		"postgres_user", info.User,
		"postgres_sslmode", info.SSLMode,
		"postgres_password_set", info.PasswordSet,
		"postgres_defaulted_fields", info.DefaultedFields,
	}
}

func (info ListenAddressInfo) LogAttrs() []any {
	return []any{
		"listen_addr_raw", info.Raw,
		"listen_addr", info.Normalized,
		"listen_addr_bare_port_normalized", info.BarePortNormalized,
	}
}

func loadEnvFiles(explicitPath string) (EnvFileReport, error) {
	explicitPath = strings.TrimSpace(explicitPath)
	if explicitPath != "" {
		return loadEnvFileList(EnvFileReport{Mode: "flag", Requested: explicitPath}, []string{explicitPath}, true)
	}

	envFilePath := strings.TrimSpace(os.Getenv(EnvFileVariable))
	if envFilePath != "" {
		return loadEnvFileList(EnvFileReport{Mode: "env", Requested: envFilePath}, []string{envFilePath}, true)
	}

	return loadEnvFileList(EnvFileReport{Mode: "auto"}, defaultEnvFileCandidates(), false)
}

func loadEnvFileList(report EnvFileReport, paths []string, required bool) (EnvFileReport, error) {
	seen := make(map[string]struct{}, len(paths))
	for _, path := range paths {
		absPath, err := filepath.Abs(path)
		if err != nil {
			return report, fmt.Errorf("resolve env file path %q: %w", path, err)
		}
		absPath = filepath.Clean(absPath)
		if _, ok := seen[absPath]; ok {
			continue
		}
		seen[absPath] = struct{}{}

		loaded, exists, err := loadEnvFile(absPath)
		if err != nil {
			return report, err
		}
		if !exists {
			if required {
				return report, fmt.Errorf("env file %q does not exist", absPath)
			}
			report.Missing = append(report.Missing, absPath)
			continue
		}
		report.Loaded = append(report.Loaded, loaded)
	}
	return report, nil
}

func defaultEnvFileCandidates() []string {
	var candidates []string
	add := func(dir string) {
		if dir == "" {
			return
		}
		candidates = append(candidates, filepath.Join(dir, "cloud-api.env"))
		candidates = append(candidates, filepath.Join(dir, ".env"))
	}

	if wd, err := os.Getwd(); err == nil {
		add(wd)
	}
	if executable, err := os.Executable(); err == nil {
		add(filepath.Dir(executable))
	}
	if runtime.GOOS != "windows" {
		candidates = append(candidates, "/etc/auto-backup-bdnetdesk/cloud-api.env")
	}
	return candidates
}

func loadEnvFile(path string) (LoadedEnvFile, bool, error) {
	file, err := os.Open(path)
	if err != nil {
		if os.IsNotExist(err) {
			return LoadedEnvFile{}, false, nil
		}
		return LoadedEnvFile{}, false, fmt.Errorf("open env file %q: %w", path, err)
	}
	defer file.Close()

	stat, err := file.Stat()
	if err != nil {
		return LoadedEnvFile{}, false, fmt.Errorf("stat env file %q: %w", path, err)
	}
	if stat.IsDir() {
		return LoadedEnvFile{}, false, fmt.Errorf("env file %q is a directory", path)
	}

	loaded := LoadedEnvFile{Path: path}
	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for lineNo := 1; scanner.Scan(); lineNo++ {
		rawLine := strings.TrimPrefix(scanner.Text(), "\ufeff")
		line := strings.TrimSpace(rawLine)
		if line == "" || strings.HasPrefix(line, "#") {
			loaded.IgnoredLineCnt++
			continue
		}
		if strings.HasPrefix(line, "export ") {
			line = strings.TrimSpace(strings.TrimPrefix(line, "export "))
		}

		key, rawValue, ok := strings.Cut(line, "=")
		if !ok {
			return LoadedEnvFile{}, false, fmt.Errorf("parse env file %q line %d: missing '='", path, lineNo)
		}
		key = strings.TrimSpace(key)
		if !isValidEnvKey(key) {
			return LoadedEnvFile{}, false, fmt.Errorf("parse env file %q line %d: invalid variable name %q", path, lineNo, key)
		}

		value, err := parseEnvValue(strings.TrimSpace(rawValue))
		if err != nil {
			return LoadedEnvFile{}, false, fmt.Errorf("parse env file %q line %d: %w", path, lineNo, err)
		}
		if os.Getenv(key) != "" {
			loaded.PreservedKeys = append(loaded.PreservedKeys, key)
			continue
		}
		if err := os.Setenv(key, value); err != nil {
			return LoadedEnvFile{}, false, fmt.Errorf("set env variable %q from %q: %w", key, path, err)
		}
		loaded.SetKeys = append(loaded.SetKeys, key)
	}
	if err := scanner.Err(); err != nil {
		return LoadedEnvFile{}, false, fmt.Errorf("read env file %q: %w", path, err)
	}
	return loaded, true, nil
}

func resolveListenAddress() (string, ListenAddressInfo) {
	raw := getenv("CLOUD_API_ADDR", ":8080")
	normalized := raw
	barePortNormalized := false
	if isBarePort(raw) {
		normalized = ":" + raw
		barePortNormalized = true
	}
	return normalized, ListenAddressInfo{
		Raw:                raw,
		Normalized:         normalized,
		BarePortNormalized: barePortNormalized,
	}
}

func isBarePort(value string) bool {
	if value == "" {
		return false
	}
	for _, r := range value {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}

func isValidEnvKey(key string) bool {
	if key == "" {
		return false
	}
	for i, r := range key {
		valid := r == '_' || r >= 'A' && r <= 'Z' || r >= 'a' && r <= 'z' || i > 0 && r >= '0' && r <= '9'
		if !valid {
			return false
		}
	}
	first := key[0]
	return first == '_' || first >= 'A' && first <= 'Z' || first >= 'a' && first <= 'z'
}

func parseEnvValue(value string) (string, error) {
	if value == "" {
		return "", nil
	}
	if strings.HasPrefix(value, `"`) {
		if !strings.HasSuffix(value, `"`) || len(value) == 1 {
			return "", fmt.Errorf("unterminated double-quoted value")
		}
		parsed, err := strconv.Unquote(value)
		if err != nil {
			return "", fmt.Errorf("invalid double-quoted value: %w", err)
		}
		return parsed, nil
	}
	if strings.HasPrefix(value, "'") {
		if !strings.HasSuffix(value, "'") || len(value) == 1 {
			return "", fmt.Errorf("unterminated single-quoted value")
		}
		return strings.TrimSuffix(strings.TrimPrefix(value, "'"), "'"), nil
	}
	return value, nil
}

func resolvePostgresConfig() (string, PostgresConnectionInfo) {
	if dsn := strings.TrimSpace(os.Getenv("POSTGRES_DSN")); dsn != "" {
		info := summarizePostgresDSN(dsn)
		info.Source = "POSTGRES_DSN"
		return dsn, info
	}

	var defaulted []string
	user, usedDefault := getenvWithDefault("POSTGRES_USER", "auto_backup_user")
	if usedDefault {
		defaulted = append(defaulted, "POSTGRES_USER")
	}
	host, usedDefault := getenvWithDefault("POSTGRES_HOST", "127.0.0.1")
	if usedDefault {
		defaulted = append(defaulted, "POSTGRES_HOST")
	}
	port, usedDefault := getenvWithDefault("POSTGRES_PORT", "5432")
	if usedDefault {
		defaulted = append(defaulted, "POSTGRES_PORT")
	}
	db, usedDefault := getenvWithDefault("POSTGRES_DB", "auto_backup_bdnetdesk")
	if usedDefault {
		defaulted = append(defaulted, "POSTGRES_DB")
	}
	sslmode, usedDefault := getenvWithDefault("POSTGRES_SSLMODE", "disable")
	if usedDefault {
		defaulted = append(defaulted, "POSTGRES_SSLMODE")
	}
	password := os.Getenv("POSTGRES_PASSWORD")

	u := url.URL{
		Scheme: "postgres",
		User:   url.UserPassword(user, password),
		Host:   net.JoinHostPort(host, port),
		Path:   "/" + db,
	}
	q := url.Values{}
	if sslmode != "" {
		q.Set("sslmode", sslmode)
	}
	u.RawQuery = q.Encode()

	return u.String(), PostgresConnectionInfo{
		Source:          "POSTGRES_*",
		DSNFormat:       "composed-url",
		Host:            host,
		Port:            port,
		Database:        db,
		User:            user,
		SSLMode:         sslmode,
		PasswordSet:     password != "",
		DefaultedFields: defaulted,
	}
}

func summarizePostgresDSN(dsn string) PostgresConnectionInfo {
	info := PostgresConnectionInfo{DSNFormat: "unknown"}
	if u, err := url.Parse(dsn); err == nil && (u.Scheme == "postgres" || u.Scheme == "postgresql") {
		info.DSNFormat = "url"
		info.Host = u.Hostname()
		info.Port = u.Port()
		info.Database = strings.TrimPrefix(u.Path, "/")
		info.User = u.User.Username()
		info.SSLMode = u.Query().Get("sslmode")
		_, info.PasswordSet = u.User.Password()
		return info
	}

	fields := parsePostgresKeywordDSN(dsn)
	if len(fields) == 0 {
		return info
	}
	info.DSNFormat = "keyword"
	info.Host = fields["host"]
	info.Port = fields["port"]
	info.Database = fields["dbname"]
	info.User = fields["user"]
	info.SSLMode = fields["sslmode"]
	info.PasswordSet = fields["password"] != ""
	return info
}

func parsePostgresKeywordDSN(dsn string) map[string]string {
	values := make(map[string]string)
	for _, field := range strings.Fields(dsn) {
		key, value, ok := strings.Cut(field, "=")
		if !ok || key == "" {
			continue
		}
		values[key] = strings.Trim(value, "'")
	}
	return values
}

func getenv(key, fallback string) string {
	value, _ := getenvWithDefault(key, fallback)
	return value
}

func getenvWithDefault(key, fallback string) (string, bool) {
	value := os.Getenv(key)
	if value == "" {
		return fallback, true
	}
	return value, false
}
