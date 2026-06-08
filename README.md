# auto_backup_bdnetdesk

Windows 桌面端百度网盘加密备份工具。

本项目目标是将用户选择的文件和文件夹，以加密压缩包形式备份到百度网盘指定位置，并通过本地 SQLite 与 Go 云端 API + PostgreSQL 双写记录，实现多设备去重、断点续传、来源映射、数据库与百度网盘校对、原始数据清理记录和恢复。

## 当前状态

项目已完成产品规格、初始化文档、Go 云端同步服务、部署构建脚本、百度网盘云端授权管理、PySide6 百度设置页、本机 Device Token/KDF 凭据、真实百度上传核心链路、本地 SQLite 上传账本、`uploadid` 断点续传、`sync_outbox` 同步 worker、脱敏联调 CLI、备份任务主 UI 与任务模型、扫描与内容指纹、内容去重索引、7-Zip AES-256 加密归档与 manifest、端到端备份编排、真实百度全链路验收、Windows 长路径文件访问硬化、缓存额度与 artifact 生命周期管理。仓库以公开项目形式发布，采用 GNU Lesser General Public License v2.1 授权。

当前仍未达到完整 v1.3 桌面端发布状态；剩余主线集中在 P2 阶段 12 原始数据清理、P2 阶段 13 恢复流程，以及 P3 阶段 14 打包发布与最终验收。按当前排期估算，离 v1.3 可交付仍约剩 20%-25%。最新差异审计和开发排期以 `docs/roadmap_progress.md` 为准。

## 文档入口

- `AGENTS.MD`：项目记忆、协作规则、编码约束、提交规则和安全约束。
- `docs/product_spec_v1.3.md`：完整产品与技术规格。
- `docs/roadmap_progress.md`：开发排期和进度记录。
- `.env.example`：本地开发环境变量示例。
- `docs/deployment_nginx_backup_baichengedu.md`：`backup.baichengedu.com`、nginx 反代和百度回调部署示例。
- `LICENSE`：GNU Lesser General Public License v2.1 授权文本。

## 云端同步服务

Go 云端服务源码集中在 `cloud-api/` 目录，入口为 `cloud-api/cmd/cloud-api`。服务默认监听 `:8080`，通过 `POSTGRES_DSN` 或 PostgreSQL 分项环境变量连接外部 PostgreSQL。`CLOUD_API_ADDR` 使用 Go 监听地址格式，推荐生产反代场景填写 `127.0.0.1:9321`；只填写裸端口如 `9321` 时，服务会自动按 `:9321` 兼容处理。

```powershell
cd cloud-api
go run ./cmd/cloud-api
```

从仓库根目录构建服务端部署二进制：

```powershell
.\go_build.ps1
```

默认生成 `dist/cloud-api/linux-amd64/cloud-api`，用于 Linux amd64 服务器部署；可通过 `-GoOS`、`-GoArch`、`-OutputDir`、`-OutputName`、`-ModuleDir` 和 `-ServiceName` 调整目标平台、输出位置和服务入口。

云端 PostgreSQL 迁移文件位于 `cloud-api/migrations/postgres`，并已嵌入服务端二进制。`cloud-api serve` 启动时会自动检查 PostgreSQL 关键 schema；缺少 `devices`、`baidu_accounts` 等关键表/列或 schema 检查失败时，会自动执行内置迁移并复查，复查仍失败才拒绝启动。

正常二进制部署只需要启动服务：

```bash
/opt/auto-backup-bdnetdesk/cloud-api serve --env-file /www/server/auto-backup-bdnetdesk/.env
```

`cloud-api migrate --env-file /path/to/.env` 保留为排障或人工修复入口，不作为正常部署的初始化前置条件。客户端本地 `sync_outbox` 迁移契约位于 `client/migrations/sqlite`。百度账号授权、密文 token、设备绑定和刷新租约表由 `cloud-api/migrations/postgres/002_baidu_auth.sql` 提供。`GET /v1/readyz` 会同时检查 PostgreSQL 可连接和关键 schema 已存在；服务启动后的缺表/缺列会返回 `schema_not_ready`。

二进制部署到服务器后，服务通过环境变量决定连接哪台 PostgreSQL。服务启动时支持三种配置来源：

- 已注入到进程的环境变量，优先级最高。
- 启动参数 `--env-file /path/to/cloud-api.env` 或环境变量 `CLOUD_API_ENV_FILE=/path/to/cloud-api.env`。
- 未显式指定时，自动尝试读取当前工作目录、二进制所在目录下的 `cloud-api.env`/`.env`，Linux 下还会尝试 `/etc/auto-backup-bdnetdesk/cloud-api.env`。

`.env`/环境文件只填充当前进程缺失的变量，不覆盖已由 systemd、宝塔面板或 Shell 注入的非空环境变量。推荐在服务器创建 `/etc/auto-backup-bdnetdesk/cloud-api.env`，并在 systemd 中使用：

```ini
[Service]
EnvironmentFile=/etc/auto-backup-bdnetdesk/cloud-api.env
ExecStart=/opt/auto-backup-bdnetdesk/cloud-api
```

如果宝塔面板的环境变量注入不可靠，可以直接在启动命令中指定：

```bash
/opt/auto-backup-bdnetdesk/cloud-api serve --env-file /www/server/auto-backup-bdnetdesk/.env
```

环境文件内容参考 `cloud-api/.env.example`。`APP_ENV=production` 只用于日志标识当前部署环境，不影响 PostgreSQL 连接选择。真实 PostgreSQL 密码、DSN、百度 App Secret 和 token 不得提交到仓库。

## 百度网盘授权

客户端不再要求用户填写百度 access token 或 refresh token。用户在客户端选择已有百度账号，或新建设备码/授权码会话：

- 设备码模式显示百度官方授权地址、用户码和二维码。
- 授权码模式通过 `https://backup.baichengedu.com/v1/baidu/oauth/callback` 接收百度回调。
- 百度账号密码只允许在百度官方 `openapi.baidu.com` 页面输入，本项目 UI 不收集、不转发百度账号密码。

云端服务完成授权换取 token 后，立即用客户端提供的密码派生 wrapping key 或 RSA 公钥加密 token，并只把密文保存到 PostgreSQL。后续客户端通过 API 获取密文 token 后在本地解密，直接调用百度网盘 API；服务端不提供解密 token 的接口。

密文 token 响应包含 `encryption_method`：

```text
password_argon2id_aes256gcm_v1
rsa_oaep_sha256_aes256gcm_v1
```

客户端据此决定使用用户密码派生密钥，还是读取用户配置的 RSA 私钥路径。RSA 备选密钥生成脚本为 `scripts/generate_baidu_rsa_keypair.ps1`，默认输出到已忽略的 `deploy-only/` 目录。

## 敏感信息

不要提交 `.env`、真实数据库连接串、百度 token、用户密码、本地 SQLite 数据库、缓存文件、日志或密钥文件。

`.env.example` 只提供占位示例。百度 access token、refresh token 和用户备份密码应由客户端使用 Windows DPAPI 或 Windows 凭据管理器保存。

## 许可证

本项目使用 GNU Lesser General Public License v2.1 授权，完整条款见 `LICENSE`。
