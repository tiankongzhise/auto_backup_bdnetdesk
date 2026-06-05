# backup.baichengedu.com 部署示例

本项目云端 API 和百度 OAuth 回调统一使用：

```text
https://backup.baichengedu.com
```

百度开放平台后台回调地址配置为：

```text
https://backup.baichengedu.com/v1/baidu/oauth/callback
```

## nginx 反代示例

```nginx
server {
    listen 443 ssl http2;
    server_name backup.baichengedu.com;

    ssl_certificate /etc/letsencrypt/live/backup.baichengedu.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/backup.baichengedu.com/privkey.pem;

    client_max_body_size 20m;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

server {
    listen 80;
    server_name backup.baichengedu.com;
    return 301 https://$host$request_uri;
}
```

## 服务端环境

`/etc/auto-backup-bdnetdesk/cloud-api.env` 至少需要包含：

```text
APP_ENV=production
LOG_LEVEL=INFO
CLOUD_API_ADDR=127.0.0.1:8080
PUBLIC_BASE_URL=https://backup.baichengedu.com
POSTGRES_DSN=postgres://replace-user:replace-password@127.0.0.1:5432/replace-db?sslmode=disable
BAIDU_APP_KEY=replace-with-baidu-app-key
BAIDU_APP_SECRET=replace-with-baidu-app-secret
BAIDU_SCOPE=basic,netdisk
BAIDU_REDIRECT_URI=https://backup.baichengedu.com/v1/baidu/oauth/callback
```

真实 PostgreSQL DSN、百度 App Secret 和任何 token 不得提交到仓库。

`APP_ENV=production` 只用于日志标识服务器环境，当前不会改变数据库连接、调试开关或安全策略。`LOG_LEVEL` 可填 `INFO`、`DEBUG`、`WARN` 或 `ERROR`；部署排查时可临时改为 `DEBUG`。

`CLOUD_API_ADDR` 是 Go `net/http` 监听地址。推荐反代部署使用 `127.0.0.1:8080` 或 `127.0.0.1:9321`，只允许 nginx/宝塔本机反代访问。只填写裸端口如 `9321` 时，服务会自动兼容为 `:9321`，表示监听所有网卡。

PostgreSQL 连接优先使用 `POSTGRES_DSN`。如果不配置 `POSTGRES_DSN`，必须完整配置以下分项变量：

```text
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_DB=replace-db
POSTGRES_USER=replace-user
POSTGRES_PASSWORD=replace-password
POSTGRES_SSLMODE=disable
```

## 宝塔面板部署要点

如果宝塔面板的“环境变量”注入不可靠，不要只依赖面板变量。把真实配置写入服务器上的环境文件，例如：

```text
/www/server/auto-backup-bdnetdesk/.env
```

然后在宝塔的启动命令或进程守护命令中显式指定：

```bash
/www/server/auto-backup-bdnetdesk/cloud-api --env-file /www/server/auto-backup-bdnetdesk/.env
```

服务也会在未显式指定时自动尝试读取当前工作目录、二进制所在目录下的 `cloud-api.env`/`.env`，Linux 下还会尝试 `/etc/auto-backup-bdnetdesk/cloud-api.env`。显式 `--env-file` 更适合宝塔，因为启动工作目录可能不稳定。

`.env` 文件只会填充当前进程缺失的变量，不覆盖宝塔、systemd 或 Shell 已经注入的非空变量。若日志中看到 `postgres_config_source=POSTGRES_*` 且 `postgres_defaulted_fields` 包含 `POSTGRES_USER`、`POSTGRES_DB`，说明进程没有拿到预期数据库配置；若看到 `env_files_loaded=[]`，说明 `.env` 文件没有被加载。

PostgreSQL 连接失败时重点查看日志字段：

```text
env_file_mode
env_files_loaded
postgres_config_source
postgres_host
postgres_port
postgres_database
postgres_user
postgres_sslmode
postgres_password_set
postgres_defaulted_fields
```

日志不会输出完整 DSN、数据库密码、百度 App Secret 或 token。

## RSA 备选模式

默认推荐客户端用密码派生 wrapping key，服务端只短暂接收 wrapping key 并立即加密百度 token，不保存用户密码或 key。

如需 RSA 模式，优先由客户端生成密钥对并只把公钥提交给服务端。部署端也提供备选脚本：

```powershell
.\scripts\generate_baidu_rsa_keypair.ps1
```

脚本输出到 `deploy-only/baidu-token-rsa/`，该目录已被 Git 忽略。该模式弱于客户端自持私钥，因为私钥如果留在服务器或项目部署目录中，会扩大泄露面。
