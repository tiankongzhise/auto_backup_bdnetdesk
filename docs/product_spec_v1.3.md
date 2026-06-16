# 执行版本 v1.3：百度网盘加密备份工具完整方案

## 1. 产品定稿

本项目做成 **Windows 单机桌面客户端 + 云端索引服务 + 百度网盘官方开放平台**。

客户端负责 UI、扫描、哈希、去重判断、加密压缩、验证、断点上传、恢复、缓存清理、本地状态管理。云端索引服务负责多设备去重、跨设备查询、数据灾备重建和最终一致性同步。百度网盘只保存加密压缩包、非敏感元数据和任务索引。

技术栈固定：

- 客户端：Python 3.12 + pywebview + 原生静态 HTML/CSS/JS，Windows 路线使用 WebView2。
- 本地数据库：SQLite。
- 云端服务：Go + PostgreSQL，使用 chi、pgx/pgxpool 和 sqlc；部署形态为单二进制 + 外部 PostgreSQL。
- HTTP：客户端使用 httpx；云端使用 Go net/http + chi。
- 加密压缩：7-Zip AES-256。
- 打包：PyInstaller 或 Nuitka。
- Rust：v1.3 不强制使用；若哈希/扫描性能不足，再用 Rust 写扩展模块。

百度网盘接入固定使用官方开放平台上传流程：

```text
precreate -> superfile2 upload -> create
```

参考文档：

- [上传能力说明](https://pan.baidu.com/union/doc/3ksg0s9ye)
- [预上传](https://pan.baidu.com/union/doc/3ksg0s9r7)
- [分片上传](https://pan.baidu.com/union/doc/nksg0s9vi)
- [创建文件](https://pan.baidu.com/union/doc/rksg0sa17)
- [获取文件列表](https://pan.baidu.com/union/doc/nksg0sat9)
- [递归获取文件列表](https://pan.baidu.com/union/doc/Zksg0sb73)

## 2. 固定边界

v1.3 支持：

- Windows 桌面 UI。
- 批量选择文件和文件夹。
- 用户设置缓存目录和缓存额度。
- 用户设置加密密码，或随机生成密码。
- 用户设置百度网盘备份目录、分片大小、最大压缩包大小、并发上传数。
- 快速内容指纹和完整哈希。
- 文件夹内容哈希和 manifest 哈希。
- 本地数据库与云端数据库双写。
- 断点续传。
- 数据库与百度网盘一致性校对。
- 原始数据清理记录。
- 恢复到原路径或手动路径。

v1.3 不支持：

- 自动定时备份。
- 非 Windows 平台。
- 非百度网盘。
- 客户端直连云端数据库。
- 上传用户明文密码。
- 默认永久删除原始数据。

## 3. 百度远端目录

用户可设置百度备份根目录，但必须位于 `/apps/{appname}` 下。默认：

```text
/apps/{appname}/backups
```

远端目录固定为按日期、设备、任务组织：

```text
/apps/{appname}/backups/
  {yyyy}/{MM}/{dd}/{device_id}/{job_id}/
    archives/
      {archive_seq}-{archive_sha256}.7z
      {archive_seq}-{archive_sha256}.meta.json
    job.meta.json
    job.index.json
```

规则：

- `yyyy/MM/dd` 使用备份任务创建日期，不使用上传完成日期。
- `device_id` 用于区分来源电脑。
- `job_id` 用于区分同一天同一电脑的多次备份。
- `archive_seq` 从 `000001` 开始，保证任务内排序。
- `archive_sha256` 放在文件名中，用于唯一性、校验和去重。
- 不再使用 `archive_sha256` 前缀作为目录分桶。
- 远端文件创建使用 `rtype=0`，不允许百度自动重命名；若冲突，进入校对流程。
- `.meta.json` 和 `job.index.json` 不保存明文原始路径、原始文件名和用户密码。

跨任务重复 archive 处理：

- 上传前查询本地库和云端库是否已有相同 `archive_sha256` 且远端校对通过。
- 若已存在，当前任务不重复上传该 archive，只在 `job.index.json` 和数据库中记录对已有远端 archive 的引用。
- 若云端不可用且本地不知道已有 archive，则允许上传；后续校对可标记为重复远端对象，但不自动删除。

## 4. UI 页面

### 4.1 备份任务页

必须支持：

- 批量选择文件和文件夹。
- 拖拽添加。
- 移除备份源。
- 显示每个任务的扫描、哈希、压缩、验证、上传、同步进度。
- 暂停、继续、取消。
- 显示断点续传状态：可继续、等待重试、远端待校对、同步待补偿。

文件夹递归扫描规则：

- 默认递归普通目录。
- 默认不跟随 symlink、junction、快捷方式。
- 遇到不可读文件，记录失败原因，不中止整个任务。

### 4.2 缓存设置页

用户必须设置：

- 缓存目录。
- 缓存最大额度 `cache_quota_gib`。

硬规则：

```text
cache_quota_gib >= 40GiB
effective_cache_budget = min(cache_quota_gib, disk_free_space - 10GiB)
effective_cache_budget >= 40GiB
```

不满足时禁止新任务开始。

UI 必须展示：

- 缓存总额度。
- 当前占用。
- 可释放缓存。
- 当前清理等级。
- 正在占用缓存的任务和文件类型。

### 4.3 密码页

支持：

- 用户手动输入密码。
- 电脑随机生成密码。
- 复制密码。
- 导出密码到 `.txt`。
- 显示密码强度。

随机密码固定为：

```text
32 字节安全随机数
Base64 显示
```

密码规则：

- 不上传云端。
- 不写日志。
- 不写 `.meta.json`。
- 用户丢失密码后无法恢复加密备份。

### 4.4 百度设置页

用户可设置：

- 选择已有百度网盘账号授权。
- 新增百度网盘账号授权，支持设备码模式和授权码回调模式。
- 设备码模式必须展示百度官方授权地址、用户码和二维码。
- 授权码模式回调地址固定为 `https://backup.baichengedu.com/v1/baidu/oauth/callback`。
- token 解密方式，默认 `password_argon2id_aes256gcm_v1`，可选 `rsa_oaep_sha256_aes256gcm_v1`。
- 备份根目录，必须在 `/apps/{appname}` 下。
- 分片大小。
- 最大压缩包大小。
- 同时上传 archive 数。
- 单个 archive 内分片上传并发数。

授权安全规则：

- 客户端不再要求用户填写百度 access token 或 refresh token。
- 百度账号密码只允许在百度官方 `openapi.baidu.com` 授权页输入，本项目 UI 不收集、不转发百度账号密码。
- 云端完成授权换取 token 后立即加密，只保存 `encrypted_token_json`、`encryption_method`、`token_version` 和过期时间。
- 服务端不提供解密 token 接口；客户端取回密文后在本地解密并直接调用百度网盘 API。
- password 模式由客户端本地用密码派生 32 字节 wrapping key，服务端只在完成授权请求中短暂接收该 key 并立即加密 token，不保存 key。
- RSA 模式由服务端使用客户端提供的 RSA 公钥加密内容密钥，响应和数据库保存 `private_key_hint`，客户端据此读取 RSA 私钥解密。
- 服务器生成 RSA 密钥对只作为部署备选，输出到 Git 忽略路径；不得把私钥提交到仓库。
- 多台电脑可以选择同一个百度账号。access token 按 OAuth Bearer Token 语义可被不同设备使用，但 refresh token 更新必须通过云端刷新租约和 `token_version` 乐观锁避免并发覆盖。

默认参数：

```text
普通用户：分片 4MiB，最大压缩包 3.8GiB
会员用户：分片 16MiB，最大压缩包 9.5GiB
超级会员：分片 32MiB，最大压缩包 19GiB
```

必须保证：

```text
ceil(archive_size / part_size) <= 1024
```

默认并发：

```text
同时上传 archive 数 = 2
单 archive 分片并发 = 4
```

### 4.5 去重策略页

默认策略：

```text
内容只存一份，所有来源路径记录引用
```

用户可选：

- 重复内容只记录引用。
- 重复内容仍独立备份。
- 发现重复时逐项确认。
- 跳过重复内容，不记录新的 payload，但保留来源明细。

重复分类必须展示：

- 名称完全一致且内容一致。
- 名称完全一致但内容不同。
- 名称不同但内容一致。
- 疑似副本名：`(1)`、`(2)`、`副本`、`copy`。
- 多台电脑出现相同内容。

最终重复判断只能使用：

```text
size + file_sha256
```

### 4.6 来源与远端映射页

必须能回答：

```text
哪台电脑的哪个路径，备份到了百度网盘哪里
```

每行展示：

- 设备名。
- `device_id`。
- 原始绝对路径。
- 文件名。
- 文件大小。
- ctime、mtime、atime。
- MD5、SHA256、content_id。
- 所属 job_id。
- 去重状态。
- archive_seq。
- archive_sha256。
- 百度 `.7z` 路径。
- 百度 `.meta.json` 路径。
- 百度 fs_id。
- 原始数据是否已清理。
- 是否可恢复。

支持按设备、日期、任务、路径、文件名、SHA256、清理状态、远端状态筛选。

### 4.7 数据库与百度校对页

校对对象：

- 本地 SQLite。
- 云端 PostgreSQL。
- 百度网盘远端 `.7z`、`.meta.json`、`job.index.json`。

差异类型固定：

```text
local_only
cloud_only
baidu_only
local_cloud_version_conflict
db_exists_remote_missing
remote_meta_missing
remote_meta_mismatch
remote_size_mismatch
fs_id_changed
archive_sha256_mismatch
remote_unreadable
```

用户处理选项固定：

- 以本地数据库更新云端数据库。
- 以云端数据库更新本地数据库。
- 以百度网盘对象重建数据库记录。
- 重新备份到百度网盘。
- 标记远端缺失。
- 保留差异，稍后处理。

重新备份优先级：

1. 缓存 archive 存在且 SHA256 匹配，直接重新上传。
2. 缓存不存在但原始文件存在且未变化，重新压缩上传。
3. 原始文件已清理且无缓存，不能重新备份，只能标记缺失或从其他设备恢复。

### 4.8 原始数据清理页

清理入口只在以下条件全部满足时启用：

```text
job_status = completed
archive_status = remote_created
meta_status = uploaded
verify_status = passed
local_db_status = complete
```

若云端同步仍是 `sync_pending`，允许清理，但 UI 必须明确显示“云端索引仍待同步”；本地记录必须完整。

清理前必须复查源文件：

```text
size
mtime_ns
file_index
```

不一致时禁止清理。

清理方式：

- 默认移入 Windows 回收站。
- 回收站失败时，可移动到用户指定隔离目录。
- 永久删除为高级选项，默认隐藏，必须二次确认。

数据库必须记录：

```text
source_cleanup_status:
none
requested
moved_to_recycle_bin
moved_to_quarantine
permanently_deleted
failed
```

同时记录：

- cleanup_time。
- cleanup_method。
- cleanup_operator。
- original_path。
- pre_cleanup_size。
- pre_cleanup_sha256。
- error_message。

### 4.9 恢复页

恢复来源：

- 按设备浏览。
- 按原始路径浏览。
- 按备份任务浏览。
- 按日期浏览。
- 按文件名或 SHA256 搜索。

恢复目标：

- 恢复到原路径。
- 恢复到用户指定路径。

原路径恢复规则：

- 当前设备等于来源设备时，可默认恢复到原路径。
- 来源设备不同，必须用户确认目标路径。
- 盘符不存在时，强制选择手动路径。
- 父目录不存在时自动创建。

冲突策略默认：

```text
保留两者，新文件追加 "restored yyyyMMdd-HHmmss"
```

可选：

- 跳过已有文件。
- 覆盖已有文件；覆盖前旧文件移入回收站。

恢复完成后必须重新计算 SHA256 并比对。

## 5. 本地与云端数据库双写

本地 SQLite 是任务执行主库。只要本地事务落盘成功，任务即可继续。客户端本地 `sync_outbox` 通过 Go 云端 Cloud Sync API 异步写入 PostgreSQL，保证最终一致。云端服务负责 revision 接收、幂等写入、版本冲突检测、跨设备查询和灾备重建，不保存用户明文密码。

所有核心实体必须作为同步实体在本地 SQLite 和云端 PostgreSQL 中可表达、可查询、可校对：

- devices
- backup_jobs
- backup_sources
- file_items
- folder_items
- content_objects
- content_references
- archives
- archive_members
- upload_sessions
- upload_parts
- remote_objects
- source_cleanup_records
- restore_jobs
- reconcile_runs

每条记录必须包含版本字段：

```text
entity_id
entity_type
schema_version
data_version
revision_id
updated_at
updated_by_device_id
sync_status
deleted_at
canonical_record_sha256
last_synced_revision_id
```

规则：

- `schema_version` 表示表结构版本。
- `data_version` 每次业务字段变化递增。
- `revision_id = UUIDv7`，每次写入生成新版本。
- `canonical_record_sha256` 使用规范化 JSON 计算。
- 删除使用 tombstone：写 `deleted_at`，不物理删除。
- 云端写入使用 `entity_id + revision_id` 幂等。

同步流程：

1. 本地业务表写入成功。
2. 同一 SQLite 事务写入 `sync_outbox`。
3. 后台同步器读取 outbox。
4. 调用 Go 云端 Cloud Sync API。
5. 云端按 revision 幂等写入。
6. 成功后本地标记 `sync_status=synced`。
7. 失败保持 `sync_pending`，稍后重试。

云端 API 固定接口：

```text
POST /v1/devices/register
POST /v1/sync/revisions
GET /v1/contents/{content_id}
GET /v1/archives/{archive_sha256}
GET /v1/reconcile/entities/{entity_id}
POST /v1/baidu/auth/sessions
GET /v1/baidu/auth/sessions/{session_id}
POST /v1/baidu/auth/sessions/{session_id}/complete
GET /v1/baidu/oauth/callback
GET /v1/baidu/accounts
POST /v1/baidu/accounts/{account_id}/select
GET /v1/baidu/accounts/{account_id}/token
PUT /v1/baidu/accounts/{account_id}/token
POST /v1/baidu/accounts/{account_id}/refresh-lease
GET /v1/devices/current
GET /v1/healthz
GET /v1/readyz
```

认证规则：

- 客户端基于本机固定特征派生稳定 `device_id` 和 `device_fingerprint_hash`，注册时提交给云端；`client_version` 只作为元数据上报，不参与 `device_id` 生成。
- 云端校验 `device_id` 必须与 `device_fingerprint_hash` 匹配，同一 `device_id` 重复注册保持幂等，并为每次注册签发新的 Device Token。
- 后续请求使用 `Authorization: Bearer <device_token>`；`GET /v1/devices/current` 可用 token 回读真实当前 `device_id`。
- 云端只保存 token 哈希，支持同一设备多个有效 token 和按 token/设备吊销。
- 客户端不得保存 PostgreSQL 连接串，不得直连云端数据库。
- 除 `GET /v1/baidu/oauth/callback` 外，百度账号授权管理接口都必须要求 Device Token 认证。

百度授权接口语义：

- `POST /v1/baidu/auth/sessions`：创建设备码或授权码会话；设备码响应只返回用户码、二维码和授权地址，不返回 `device_code`。
- `GET /v1/baidu/auth/sessions/{session_id}`：轮询授权会话状态。
- `GET /v1/baidu/oauth/callback`：只记录百度回调的 `code/state/error`，不在公开回调中换取或返回 token。
- `POST /v1/baidu/auth/sessions/{session_id}/complete`：由已认证客户端完成 OAuth token 交换、token 加密入库和账号绑定。
- `GET /v1/baidu/accounts`：列出可选百度账号的设备级授权/绑定行，条目必须包含授权所属 `device_id` 和 `current_device` 标记；客户端用设备 ID 摘要区分多台电脑，不得只显示“当前设备”。
- `POST /v1/baidu/accounts/{account_id}/select`：当前设备选择已有百度账号。
- `GET /v1/baidu/accounts/{account_id}/token`：返回密文 token envelope，必须包含 `encryption_method` 和 `token_version`。
- `PUT /v1/baidu/accounts/{account_id}/token`：客户端刷新并重新加密 token 后回写，必须携带 `expected_token_version`。
- `POST /v1/baidu/accounts/{account_id}/refresh-lease`：获取短租约，避免多设备同时刷新同一个百度账号 token。

术语边界：

- `sync_outbox` 只存在于客户端本地 SQLite。
- 云端 Go 服务不是“远端 outbox”，而是 Cloud Sync API / Revision Ingest。
- 客户端后台同步器负责读取本地 outbox、调用云端 API、根据逐条结果更新本地同步状态。
- 云端 Go 服务负责认证、revision 幂等接收、PostgreSQL 落库、版本冲突检测和查询。

客户端 `sync_outbox` 表字段固定：

```text
event_id
entity_type
entity_id
revision_id
operation
payload_json
status
retry_count
next_retry_at
last_error
created_at
updated_at
```

`sync_outbox.status` 固定：

```text
pending
syncing
synced
sync_conflict
retryable
failed_terminal
```

`operation` 固定：

```text
upsert
delete
```

本地写入约束：

- 业务表写入和 `sync_outbox` 写入必须在同一个 SQLite 事务中完成。
- `event_id` 全局唯一。
- `entity_id + revision_id` 本地唯一，用于防止同一 revision 被重复入队。
- 后台同步器只读取 `pending` 和到期的 `retryable` 事件。
- 云端返回 `synced` 或 `duplicate` 后，本地业务记录标记 `sync_status=synced`，outbox 标记 `synced`。
- 云端返回 `conflict` 后，本地业务记录和 outbox 均进入 `sync_conflict`。
- 云端不可用或返回可重试错误时，本地不得误标记 synced，必须增加 `retry_count` 并写入 `next_retry_at`。

Go 云端服务运行约束：

- 服务端代码必须集中在 `cloud-api/` 目录，不得散放到仓库根目录。
- 服务入口固定为 `cloud-api/cmd/cloud-api`。
- 默认监听 `CLOUD_API_ADDR=:8080`。生产反代场景推荐使用 `127.0.0.1:8080` 或 `127.0.0.1:9321`，只暴露给本机 nginx/宝塔反代；裸端口如 `9321` 会被服务自动兼容为 `:9321`。
- 对外服务域名固定为 `backup.baichengedu.com`，生产 `PUBLIC_BASE_URL=https://backup.baichengedu.com`。
- PostgreSQL 连接优先使用 `POSTGRES_DSN`；未设置时使用 `POSTGRES_HOST`、`POSTGRES_PORT`、`POSTGRES_DB`、`POSTGRES_USER`、`POSTGRES_PASSWORD`、`POSTGRES_SSLMODE` 组合。
- 百度开放平台配置由服务端环境变量提供：`BAIDU_APP_KEY`、`BAIDU_APP_SECRET`、`BAIDU_SCOPE`、`BAIDU_REDIRECT_URI`、`BAIDU_AUTHORIZE_URL`、`BAIDU_DEVICE_CODE_URL`、`BAIDU_TOKEN_URL`、`BAIDU_USERINFO_URL`。
- 二进制部署到服务器后，通过进程环境变量、systemd `EnvironmentFile=/etc/auto-backup-bdnetdesk/cloud-api.env`、启动参数 `--env-file /path/to/cloud-api.env` 或 `CLOUD_API_ENV_FILE=/path/to/cloud-api.env` 决定连接哪台 PostgreSQL。
- 服务启动时 `.env`/环境文件只填充缺失变量，不覆盖 systemd、宝塔面板或 Shell 已注入的非空环境变量。
- 未显式指定环境文件时，服务自动尝试当前工作目录和二进制所在目录下的 `cloud-api.env`/`.env`，Linux 下额外尝试 `/etc/auto-backup-bdnetdesk/cloud-api.env`。
- 环境文件内容参考 `cloud-api/.env.example`，真实 PostgreSQL DSN 和密码不得提交到仓库。
- `APP_ENV=production` 只用于结构化日志标识当前部署环境，不改变 PostgreSQL 连接选择或安全策略。
- 云端 PostgreSQL 迁移位于 `cloud-api/migrations/postgres`。
- 云端服务端二进制内置 PostgreSQL 迁移，`cloud-api serve` 启动时必须自动检查 schema，缺少关键表/列或检查失败时自动执行内置迁移并复查。
- `cloud-api migrate --env-file /path/to/.env` 仅作为排障或人工修复入口，不得作为二进制正常部署后的初始化前置条件。
- 客户端本地 SQLite `sync_outbox` 迁移位于 `client/migrations/sqlite`，不属于 Go 服务端迁移。
- 结构化日志不得输出 Device Token、百度 token、用户密码、PostgreSQL 密码、完整 DSN、明文原始路径或完整敏感 payload。
- PostgreSQL 启动和连接失败日志必须包含脱敏后的 `env_file_mode`、`env_files_loaded`、`postgres_config_source`、`postgres_host`、`postgres_port`、`postgres_database`、`postgres_user`、`postgres_sslmode`、`postgres_password_set` 和 `postgres_defaulted_fields`，用于部署排查。
- `GET /v1/healthz` 只表示进程存活。
- `GET /v1/readyz` 必须检查 PostgreSQL 可用性和关键 schema 是否已迁移；服务已启动但缺少关键表/列时返回 `schema_not_ready`，不得只因数据库可 ping 就返回 ready。

systemd 示例：

```ini
[Service]
EnvironmentFile=/etc/auto-backup-bdnetdesk/cloud-api.env
ExecStart=/opt/auto-backup-bdnetdesk/cloud-api
```

宝塔或其他进程守护工具如果环境变量注入不稳定，优先使用：

```text
ExecStart=/opt/auto-backup-bdnetdesk/cloud-api --env-file /www/server/auto-backup-bdnetdesk/.env
```

nginx 对外反代：

```text
server_name backup.baichengedu.com
proxy_pass http://127.0.0.1:8080
proxy_set_header Host $host
proxy_set_header X-Forwarded-Proto $scheme
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for
```

设备注册接口：

```text
POST /v1/devices/register
```

请求字段：

```text
device_id
device_fingerprint_hash
device_name
hostname
os_version
client_version
```

响应字段：

```text
device_id
device_token
```

规则：

- `device_id` 必填，由客户端基于本机固定特征派生，格式为 `dev_` 前缀的稳定摘要 ID。
- `device_fingerprint_hash` 必填，必须是 64 位小写 SHA256 十六进制；云端会校验它与 `device_id` 的派生关系。
- `device_name` 必填。
- `device_token` 只在注册响应中返回一次。
- 同一 `device_id` 可重复注册并获得新的 Device Token，旧 token 继续有效，便于不同 client 版本升级/回滚。
- 云端只保存 `device_token` 的 SHA256 哈希，并通过 `GET /v1/devices/current` 支持用 token 回读真实当前设备。
- token 被吊销后，所有需要认证的接口必须返回 401。

批量 revision 同步接口：

```text
POST /v1/sync/revisions
```

请求字段：

```text
events[]
  event_id
  entity_type
  entity_id
  revision_id
  schema_version
  data_version
  operation
  canonical_record_sha256
  payload
  updated_at
  deleted_at
```

规则：

- 单次请求最多提交 100 条事件。
- `schema_version > 0`。
- `data_version > 0`。
- `canonical_record_sha256` 必须为 64 位小写十六进制 SHA256。
- `payload` 必须是合法 UTF-8 JSON。
- `delete` 操作必须使用 tombstone，不物理删除云端业务记录。

响应字段：

```text
results[]
  event_id
  entity_id
  revision_id
  status
  reason
  cloud_data_version
  cloud_revision_id
```

`status` 固定：

```text
synced
duplicate
conflict
rejected
```

同步结果语义：

- `synced`：云端已接受并写入当前 revision。
- `duplicate`：云端已存在相同 `event_id` 或相同 `entity_id + revision_id`，客户端可安全视为成功。
- `conflict`：云端已有更高版本，或同版本但 revision/hash 不一致；客户端必须进入校对流程。
- `rejected`：事件字段非法或业务索引 payload 缺少必填字段；客户端不得重试同一无效 payload。
- PostgreSQL 不可用时接口返回 503 和 `retryable_error`，客户端保持 `sync_pending` 或转入 `retryable`。

云端 PostgreSQL 表：

- `devices`：设备身份、token 哈希、吊销状态、最后访问时间。
- `cloud_entities`：所有核心同步实体的当前版本投影。
- `entity_revisions`：所有已接收 revision 的不可变记录，用于幂等、审计和冲突定位。
- `content_objects`：内容去重索引，唯一键为 `content_id`。
- `archive_objects`：归档去重索引，唯一键为 `archive_sha256`。

数据库一致性口径：

- 当前 Go 云端数据库是 revision 投影与审计层，不等同于已经为所有本地业务表建立同名云端物理表。
- 所有核心实体必须作为同步实体在本地 SQLite 和云端 PostgreSQL 中可表达、可查询、可校对。
- v1.3 一致性校对以 `entity_id`、`revision_id`、`data_version`、`canonical_record_sha256` 为准。
- `cloud_entities` 保存每个同步实体的当前版本 JSONB 投影，`entity_revisions` 保存不可变 revision 历史。
- `content_objects` 和 `archive_objects` 是为了跨设备去重与远端校对额外建立的索引表。
- 后续 SQLite schema 阶段必须补齐本地核心业务表，并明确云端继续使用 JSONB 投影、增加同名物理表，或增加视图/索引表的最终方案。
- 校对页不得仅凭“是否存在同名云端物理表”判断本地/云端一致；必须按 revision、版本号和规范化记录哈希判断。

云端唯一约束：

```text
devices.device_token_hash
entity_revisions.event_id
entity_revisions(entity_id, revision_id)
content_objects.content_id
archive_objects.archive_sha256
```

云端写入规则：

- 写入 revision 前必须按 `entity_id` 串行化同一实体的并发写入。
- 相同 `event_id` 重复提交不得重复写业务状态。
- 相同 `entity_id + revision_id` 重复提交不得重复写业务状态。
- 云端当前 `data_version` 大于客户端提交版本时，返回 `conflict`。
- 云端当前 `data_version` 等于客户端提交版本但 `revision_id` 和 `canonical_record_sha256` 不一致时，返回 `conflict`。
- `content_objects` 事件必须在 payload 中包含 `content_id`、`file_sha256`、`size_bytes`。
- `archives` 或 `archive_objects` 事件必须在 payload 中包含 `archive_sha256`。

查询接口语义：

- `GET /v1/contents/{content_id}`：查询云端是否已有内容对象，用于跨设备去重候选判断。
- `GET /v1/archives/{archive_sha256}`：查询云端是否已有 archive，用于避免重复上传和校对远端状态。
- `GET /v1/reconcile/entities/{entity_id}`：返回云端当前实体版本和最近 revision 摘要，用于本地/云端冲突定位。
- 查询接口必须要求 Device Token 认证。

冲突规则：

- 云端已有更高 `data_version` 时，客户端进入 `sync_conflict`。
- 不自动覆盖。
- 用户在校对页选择本地为准、云端为准或按百度实际状态重建。

灾备重建：

- 本地库损坏：用云端数据库 + 百度 `.meta.json` + 加密 manifest 重建本地库。
- 云端库损坏：用本地 SQLite + 百度 `.meta.json` 重建云端库。
- 本地和云端都缺，但百度对象存在：导入 `.meta.json` 为 `baidu_only`，用户输入密码读取加密 manifest 后补全来源明细。
- 百度对象缺失但数据库存在：标记 `remote_missing`，用户选择重新备份或保留缺失记录。

## 6. 内容指纹与哈希

内容指纹只描述文件字节内容，不包含：

- 文件名。
- 路径。
- ctime。
- mtime。
- atime。
- 设备名。
- device_id。

完整文件身份固定为：

```text
file_md5 = md5(full_bytes)
file_sha256 = sha256(full_bytes)
content_id = sha256("v1:file:" + size + ":" + file_sha256)
```

快速指纹只用于候选重复筛选，不能作为最终去重依据。

快速采样规则：

```text
<=16MiB       完整读取
16MiB-256MiB  4 个 1MiB 采样块
256MiB-1GiB   8 个 1MiB 采样块
1GiB-8GiB     16 个 1MiB 采样块
8GiB-64GiB    32 个 1MiB 采样块
>64GiB        64 个 1MiB 采样块
```

采样位置：

- 必采头部。
- 必采尾部。
- 中间采样点按文件大小均匀分布。
- offset 按 4KiB 向下对齐。

快速指纹公式：

```text
quick_fingerprint =
sha256("v1:qf:" + size + ":" + sample_count + ":" + ordered_sample_sha256_list)
```

文件夹哈希：

```text
folder_content_hash =
sha256(sorted_multiset(child_type + child_content_id + count))
```

用于判断目录内容是否相同，不包含路径和名称。

```text
folder_manifest_hash =
sha256(sorted_entries(relative_path + name + type + size + ctime + mtime + atime + attrs + content_id))
```

用于恢复和目录结构一致性判断。

## 7. Manifest 与 archive 内容

每个 job 必须生成加密 manifest。manifest 存在于 7-Zip 加密压缩包内。

archive 内部结构固定：

```text
/manifest/manifest.json
/payload/{content_id}
```

规则：

- 新内容写入 `/payload/{content_id}`。
- 重复内容不重复写 payload。
- 重复内容在 manifest 中记录引用：
  - content_id。
  - source_archive_id。
  - source_archive_remote_path。
  - source_member_path。
- 如果一个 job 没有新增 payload，也必须生成一个 manifest-only archive，用于保存该 job 的加密 manifest。

manifest 必填：

- job_id。
- manifest_version。
- created_at。
- device_id。
- hostname。
- os_version。
- source_roots。
- item_id。
- item_type。
- original_name。
- relative_path。
- size。
- ctime_ns。
- mtime_ns。
- atime_ns。
- Windows attributes。
- quick_fingerprint。
- md5。
- sha256。
- content_id。
- duplicate_status。
- duplicate_of_content_id。
- archive_id。
- archive_member_path。
- referenced_archive_id。
- referenced_archive_remote_path。

远端 `.meta.json` 只保存非敏感信息：

- archive_id。
- archive_seq。
- archive_sha256。
- archive_md5。
- archive_size。
- archive_type：payload、manifest_only、mixed。
- job_id。
- device_id。
- manifest_id。
- created_at。
- client_version。

## 8. 去重实现

默认去重级别是内容级去重。

本地/云端 `content_objects` 唯一键：

```text
content_id
```

`content_references` 记录：

- user_id。
- device_id。
- job_id。
- source_path_encrypted。
- original_name_encrypted。
- content_id。
- file_sha256。
- size。
- cleanup_status。
- restore_status。

多台电脑备份同一文件：

- content_id 相同。
- content_object 只创建一条。
- content_reference 创建多条。
- UI 展示多个来源。
- 恢复时按 manifest 和 reference 还原到对应路径。

多设备并发竞态：

- 云端 `content_id` 唯一约束。
- 插入冲突时读取已有 content_object。
- 不报错，不重复创建内容对象。

## 9. 缓存策略

缓存结构固定：

```text
{cache_root}/jobs/{job_id}/manifest_plain/
{cache_root}/jobs/{job_id}/archives/
{cache_root}/jobs/{job_id}/verify/
{cache_root}/jobs/{job_id}/download/
{cache_root}/jobs/{job_id}/restore/
{cache_root}/jobs/{job_id}/tmp/
```

缓存 artifact 必须记录到 `cache_artifacts`：

- artifact_id。
- job_id。
- artifact_type。
- path。
- size。
- required_until_stage。
- deletable。
- created_at。
- last_accessed_at。

清理等级：

**充足**

```text
cache_used <= 60% cache_quota
且 disk_free >= 2 * max_archive_size + 10GiB
```

行为：

- 保留完成 archive 7 天。
- 保留下载缓存 7 天。
- 删除明文 manifest、verify、tmp。

**中等**

```text
60% < cache_used <= 80%
或 disk_free < 2 * max_archive_size + 10GiB
```

行为：

- archive 远端确认并同步后删除。
- 删除下载缓存。
- 保留必要状态和日志。

**紧张**

```text
80% < cache_used <= 90%
或 disk_free < 1.5 * max_archive_size + 10GiB
```

行为：

- artifact 过了必须阶段立即删除。
- 同时只允许 1 个 archive 处于压缩/上传链路。
- 暂停新的压缩，优先上传和清理已有 archive。

**临界**

```text
cache_used > 90%
或 disk_free < max_archive_size + 10GiB
```

行为：

- 暂停扫描、哈希、压缩。
- 允许已开始上传完成。
- 完成后立即清理可删除缓存。
- 仍不足时要求用户扩大缓存或手动清理。

必须立即删除：

- 明文 manifest：压缩包验证通过后删除。
- verify 解压目录：严格验证结束后删除。
- tmp 文件：阶段结束删除。

不得删除：

- 未完成上传的 archive。
- 未上传 `.meta.json` 的 archive。
- 断点续传所需 SQLite 状态。
- 用户原始文件。

## 10. 状态机

文件状态：

```text
discovered
scanned
quick_hashed
full_hashed
dedupe_checked
packaged
skipped_duplicate
archive_hashed
verified
upload_precreated
parts_uploaded
remote_created
completed
```

验证状态：

```text
not_started
standard_test_started
standard_test_passed
strict_extract_started
strict_extract_hash_checked
strict_extract_cleanup_done
failed
not_requested
```

上传分片状态：

```text
pending
uploading
uploaded
confirmed
failed_retryable
failed_terminal
```

同步状态：

```text
local_committed
sync_pending
syncing
synced
sync_conflict
sync_failed_retryable
```

异常状态：

```text
resume_pending
changed_during_backup
remote_missing
remote_mismatch
source_cleaned
source_cleanup_failed
restore_completed
restore_failed
```

## 11. 断点续传

重启后必须恢复以下内容：

- 已扫描且源文件未变：不重新扫描。
- 已快速指纹且源文件未变：复用快速指纹。
- 已完整哈希且 `volume_id + file_index + size + mtime_ns` 未变：复用完整哈希。
- archive 存在且 SHA256 匹配：不重新压缩。
- archive 已标准校验且 SHA256 匹配：不重新验证。
- uploadid 有效：继续上传缺失分片。
- uploadid 失效：重新 precreate，只上传百度返回的缺失分片。
- 远端已创建但本地未完成：通过百度列表和 `.meta.json` 校对后补写本地状态。

上传恢复步骤：

1. 校验本地 archive SHA256。
2. 查询本地 remote_objects。
3. 若远端已存在，校对 path、size、fs_id、meta。
4. 若校对通过，标记 remote_created。
5. 若远端不存在，调用 precreate。
6. 使用百度返回的缺失 block_list 上传分片。
7. 分片完成后调用 create。
8. 上传 `.meta.json`。
9. 写本地完成状态和 sync_outbox。

重试退避：

```text
2s -> 5s -> 15s -> 60s -> 180s
```

最多 5 次，超过后进入 `failed_retryable`，等待用户继续。

## 12. 备份流程

完整执行顺序固定：

1. 用户选择源文件/文件夹。
2. 用户设置缓存目录和缓存额度。
3. 用户设置密码或随机生成密码。
4. 用户选择已有百度账号授权，或通过设备码/授权码新增云端密文授权。
5. 用户设置去重策略。
6. 本地 SQLite 创建 job，写入 sync_outbox。
7. 本地落盘成功后任务开始。
8. 后台异步同步云端。
9. 检查缓存有效预算至少 40GiB。
10. 检查百度云端密文授权、客户端本地解密能力和容量。
11. 扫描源路径，只读元数据。
12. 计算快速指纹。
13. 查询本地/云端候选重复。
14. 计算完整 MD5/SHA256。
15. 用 size + SHA256 判定最终重复。
16. 生成明文 manifest 临时文件。
17. 按最大压缩包大小分组。
18. 生成 7-Zip AES-256 archive。
19. 计算 archive MD5/SHA256。
20. 计算百度分片 MD5 列表。
21. 执行标准验证。
22. 删除明文 manifest。
23. 执行百度断点上传。
24. 上传 `.meta.json`。
25. 上传或更新 `job.index.json`。
26. 校对远端对象。
27. 本地写 completed。
28. 写 sync_outbox。
29. 云端最终同步。
30. 按缓存等级清理。
31. UI 开放原始数据清理和恢复。

## 13. 验证策略

默认验证模式：

```text
标准验证
```

标准验证包含：

- 7-Zip test。
- archive SHA256 校验。
- manifest 存在性校验。
- `.meta.json` 生成校验。

严格验证由用户开启，包含：

- 完整解压到 verify 目录。
- 逐文件计算 SHA256。
- 与 manifest 对比。
- 验证通过后删除 verify 目录。
- 记录 `strict_extract_cleanup_done`。

标准验证未通过不得上传。严格验证开启后未通过不得上传。

## 14. 数据库与百度校对

校对流程：

1. 读取本地 remote_objects。
2. 读取云端 archive_objects。
3. 调用百度 list/listall 获取远端对象。
4. 校对 `.7z` 是否存在。
5. 校对 `.meta.json` 是否存在。
6. 校对 size。
7. 校对 archive_sha256。
8. 校对 fs_id。
9. 生成差异清单。
10. 用户选择修复方式。

递归列表接口必须限速：

```text
每分钟不超过 8 次
```

修复结果必须写入本地数据库、sync_outbox，并异步同步云端。

## 15. 原始数据清理

清理只由用户手动触发，不自动触发。

清理前条件：

```text
备份 completed
远端 remote_created
meta uploaded
验证 passed
本地数据库 complete
源文件未变化
```

源文件未变化判定：

```text
volume_id
file_index
size
mtime_ns
```

清理方式：

- 默认移入回收站。
- 可选移动到隔离目录。
- 高级选项永久删除，必须二次确认。

清理结果本地和云端都必须记录版本化记录。若云端同步失败，本地保留 `sync_pending`。

## 16. 恢复流程

恢复顺序：

1. 用户选择恢复对象。
2. 用户选择原路径或手动路径。
3. 用户输入密码。
4. 优先查找本地缓存 archive。
5. 缓存没有则从百度下载 `.7z` 和 `.meta.json`。
6. 校验 `.meta.json`。
7. 校验 archive SHA256。
8. 执行 7-Zip test。
9. 解压到 restore tmp。
10. 按 manifest 复制到目标路径。
11. 计算恢复文件 SHA256。
12. SHA256 一致则完成。
13. 清理 restore tmp。
14. 下载 archive 按缓存策略保留或删除。

若 manifest 引用了外部 archive：

- 自动下载或复用被引用 archive。
- 提取 `/payload/{content_id}`。
- 恢复到当前 manifest 记录的目标路径。

## 17. 并发与竞态控制

固定规则：

- SQLite 单 writer。
- 本地状态更新带 version 乐观锁。
- 云端写入使用 `entity_id + revision_id` 幂等。
- 云端 `content_id` 唯一。
- 云端 `archive_sha256` 唯一。
- 每个 job 使用独立缓存目录。
- 压缩前复查源文件是否变化。
- 百度 create 阶段 block_list 必须按 partseq 排序。
- 清理任务不得删除源文件，源文件清理只能走“原始数据清理页”。

并发默认：

```text
快速采样：SSD 4，HDD 1
完整哈希：1
压缩：1
同时上传 archive：2
单 archive 分片上传：4
```

## 18. 验收标准

必须通过以下场景：

- 批量选择文件和文件夹完成备份。
- 缓存有效预算低于 40GiB 时任务不能开始。
- 用户能随机生成、复制、导出密码。
- 快速指纹不包含路径、名称、时间。
- 大文件动态采样。
- 最终去重只使用完整 SHA256。
- 文件夹内容相同但路径不同，content hash 一致，manifest hash 不一致。
- 重复文件默认只上传一份 payload。
- 多台电脑同内容只创建一个 content_object，多条 reference。
- 上传中断后重启，只上传缺失分片。
- uploadid 失效后重新 precreate 并续传。
- 本地数据库落盘成功、云端断网时任务继续。
- 云端恢复后 outbox 自动补偿同步。
- 本地/云端版本不一致时能定位冲突 revision。
- 可从云端 + 百度重建本地库。
- 可从本地 + 百度重建云端库。
- 来源映射页能展示设备、原始路径、百度远端路径。
- 校对页能识别数据库与百度不一致，并允许更新数据库或重新备份。
- 备份完成后用户可清理原始数据，数据库记录清理状态。
- 源文件被修改后清理按钮禁用。
- 缓存充足时保留可复用 archive。
- 缓存中等时清理完成 archive。
- 缓存紧张时远端确认后立即清理 archive。
- 缓存临界时暂停新压缩任务。
- 恢复到原路径后 SHA256 一致。
- 恢复到手动路径后 SHA256 一致。
- 恢复冲突默认保留两者，不覆盖现有文件。
