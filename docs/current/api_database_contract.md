# API 与数据库契约

本文是 v1.3 后续开发的接口和数据库对照契约。凡修改、修复或新增云端 HTTP API、pywebview bridge API、百度网盘调用、SQLite 表结构、PostgreSQL 表结构、`sync_outbox` payload 或版本字段，必须先对齐本文，再修改代码。

本文不替代源码和迁移文件。若发现本文与真实代码、迁移或官方 API 文档不一致，应先更新本文、`document_change_audit.md` 和 `roadmap_progress.md`，再做实现变更。

## 依据与维护

| 类别 | 当前权威来源 | 本文记录范围 |
| --- | --- | --- |
| 云端 HTTP API | `cloud-api/internal/cloudapi/server.go`、`types.go`、`baidu_types.go`、`store.go` | `/v1` 路由、认证、请求/响应包络、典型错误。 |
| 云端数据库 | `cloud-api/migrations/postgres/*.sql`、`postgres_store.go`、`index_payload.go` | PostgreSQL 表结构、关键索引、投影规则、schema readiness。 |
| 客户端 bridge API | `client/src/auto_backup_client/webview_bridge.py`、`client/src/auto_backup_client/webui/js/api.js` | `window.pywebview.api` 调用契约、返回包络、operation 轮询。 |
| 本地数据库 | `client/migrations/sqlite/*.sql`、`sqlite_store.py` | SQLite 表结构、版本字段、outbox 写入责任、敏感字段过滤。 |
| 百度网盘 API | `docs/baidu_netdisk_openapi_reference.md`、`client/src/auto_backup_client/baidu/upload.py` | 本项目实际使用的官方接口、关键参数、成功/错误判断。 |

本轮核验记录：2026-06-17 浏览/搜索工具未能直接打开百度官方页面；沙箱内 `curl.exe` 访问 `pan.baidu.com` 被拦截；按权限流程提升后，`curl.exe -L` 成功获取官方“预上传”页面 `https://pan.baidu.com/union/doc/3ksg0s9r7` HTML。其余百度接口字段沿用仓库已保存的 2026-06-05/2026-06-07 官方离线摘录和当前代码实现，不伪称本轮逐页重新核验。

## 通用约束

- JSON 请求体使用 `Content-Type: application/json`；百度官方上传接口除分片外多为 `application/x-www-form-urlencoded`。
- 云端 HTTP API 错误统一返回 JSON：`{"error":"错误码","message":"脱敏说明"}`。
- 云端认证使用 `Authorization: Bearer <Device Token>`。`Device Token` 不得写入日志、文档、测试 fixture 或 UI。
- pywebview bridge 成功统一返回 `{"ok": true, "data": {...}}`；失败统一返回 `{"ok": false, "error": {"type": "...", "message": "..."}}`。
- 长操作必须返回 `operation` DTO，再由前端调用 `get_operation(operation_id)` 轮询。
- 本地 SQLite 是任务执行主库；云端 PostgreSQL 是 revision 投影和跨设备回读，不承担客户端任务执行。
- 所有同步实体必须有 `schema_version`、`data_version`、`revision_id`、`updated_at`、`updated_by_device_id`、`sync_status`、`deleted_at`、`canonical_record_sha256`、`last_synced_revision_id`。
- `canonical_record_sha256` 计算时必须排除控制字段和本地敏感字段；当前排除字段见 `sqlite_store.py` 的 `CANONICAL_CONTROL_FIELDS` 与 `LOCAL_ONLY_SYNC_FIELDS`。
- 本地路径、归档路径、目标路径、`uploadid`、明文 manifest、Device Token、百度 token、授权密码、归档密码和 wrapping key 不得进入云端 payload、UI DTO 或日志正文。

## 云端 HTTP API

### 认证与错误包络

除 `POST /v1/devices/register`、`GET /v1/healthz`、`GET /v1/readyz`、`GET /v1/baidu/oauth/callback` 外，所有 `/v1` 接口都需要 Device Token。

```http
Authorization: Bearer bdn_xxx
```

典型认证错误：

```json
{
  "error": "unauthorized",
  "message": "missing bearer token"
}
```

典型存储错误：

```json
{
  "error": "retryable_error",
  "message": "cloud store is unavailable"
}
```

### 设备与健康检查

| 方法 | 路径 | 认证 | 用途 |
| --- | --- | --- | --- |
| `POST` | `/v1/devices/register` | 否 | 注册或重复注册稳定设备，并签发新 Device Token。 |
| `GET` | `/v1/devices/current` | 是 | 回读当前 Device Token 对应设备。 |
| `GET` | `/v1/healthz` | 否 | 进程存活检查。 |
| `GET` | `/v1/readyz` | 否 | PostgreSQL 可连接且关键 schema 就绪检查。 |

#### `POST /v1/devices/register`

请求：

```json
{
  "device_id": "dev_01234567-89ab-cdef-0123-456789abcdef",
  "device_fingerprint_hash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "device_name": "ThinkBook",
  "hostname": "DESKTOP-001",
  "os_version": "Windows 11",
  "client_version": "1.3.0"
}
```

参数约束：

- `device_id` 必填，必须等于 `dev_` + fingerprint hash 的固定切片格式：`dev_{0:8}-{8:12}-{12:16}-{16:20}-{20:32}`。
- `device_fingerprint_hash` 必填，64 位小写 hex SHA256。
- `device_name` 必填。
- `hostname`、`os_version`、`client_version` 是元数据，不参与 `device_id` 派生。

成功 `201`：

```json
{
  "device_id": "dev_01234567-89ab-cdef-0123-456789abcdef",
  "device_token": "bdn_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
}
```

典型错误：

| HTTP | `error` | 触发条件 |
| --- | --- | --- |
| 400 | `invalid_json` | JSON 无法解析或包含未知字段。 |
| 400 | `invalid_device_id` | 缺失或与 fingerprint hash 不匹配。 |
| 400 | `invalid_device_fingerprint_hash` | 非 64 位小写 hex。 |
| 400 | `invalid_device_name` | 设备名为空。 |
| 403 | `device_revoked` | 已撤销设备重复注册。 |
| 409 | `device_fingerprint_conflict` | 同一 `device_id` 已绑定其他 fingerprint。 |

#### `GET /v1/devices/current`

成功 `200`：

```json
{
  "device_id": "dev_01234567-89ab-cdef-0123-456789abcdef",
  "device_name": "ThinkBook",
  "hostname": "DESKTOP-001",
  "os_version": "Windows 11",
  "client_version": "1.3.0"
}
```

#### `GET /v1/healthz`

成功 `200`：

```json
{
  "status": "ok"
}
```

#### `GET /v1/readyz`

成功 `200`：

```json
{
  "status": "ready"
}
```

schema 未就绪 `503`：

```json
{
  "error": "schema_not_ready",
  "message": "database schema is not migrated",
  "missing_tables": ["devices"],
  "missing_columns": ["devices.device_fingerprint_hash"]
}
```

### Cloud Sync 与回读

| 方法 | 路径 | 认证 | 用途 |
| --- | --- | --- | --- |
| `POST` | `/v1/sync/revisions` | 是 | 接收本地 `sync_outbox` revision。 |
| `GET` | `/v1/reconcile/entities/{entity_id}` | 是 | 回读云端当前实体和最近 revision，用于真实性审计。 |
| `GET` | `/v1/contents/{content_id}` | 是 | 查询内容级去重索引投影。 |
| `GET` | `/v1/archives/{archive_sha256}` | 是 | 查询归档对象投影。 |
| `GET` | `/v1/backups?device_id=current/all&limit=5000` | 是 | 拉取当前设备或同百度账号可见的跨设备云端历史。 |

#### `POST /v1/sync/revisions`

请求：

```json
{
  "events": [
    {
      "event_id": "evt_111",
      "entity_type": "backup_jobs",
      "entity_id": "backup_jobs:job_001",
      "revision_id": "018fe9c0-0000-7000-8000-000000000001",
      "schema_version": 1,
      "data_version": 3,
      "operation": "upsert",
      "canonical_record_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "payload": {
        "backup_job_id": "job_001",
        "entity_id": "backup_jobs:job_001",
        "device_id": "dev_01234567-89ab-cdef-0123-456789abcdef",
        "job_name": "照片备份",
        "status": "completed",
        "schema_version": 1,
        "data_version": 3,
        "revision_id": "018fe9c0-0000-7000-8000-000000000001",
        "canonical_record_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        "updated_at": "2026-06-17T08:00:00Z",
        "updated_by_device_id": "dev_01234567-89ab-cdef-0123-456789abcdef"
      },
      "updated_at": "2026-06-17T08:00:00Z"
    }
  ]
}
```

请求约束：

- `events` 必须 1-100 条。
- `operation` 只能是 `upsert` 或 `delete`。
- `schema_version > 0`，`data_version > 0`。
- `canonical_record_sha256` 必须 64 位小写 hex。
- `payload` 必须是合法 UTF-8 JSON。
- 同一 `event_id` 或同一 `entity_id + revision_id` 重复提交必须幂等。

成功 `200`：

```json
{
  "results": [
    {
      "event_id": "evt_111",
      "entity_id": "backup_jobs:job_001",
      "revision_id": "018fe9c0-0000-7000-8000-000000000001",
      "status": "synced"
    }
  ]
}
```

重复提交：

```json
{
  "results": [
    {
      "event_id": "evt_111",
      "entity_id": "backup_jobs:job_001",
      "revision_id": "018fe9c0-0000-7000-8000-000000000001",
      "status": "duplicate"
    }
  ]
}
```

冲突：

```json
{
  "results": [
    {
      "event_id": "evt_222",
      "entity_id": "backup_jobs:job_001",
      "revision_id": "018fe9c0-0000-7000-8000-000000000002",
      "status": "conflict",
      "cloud_data_version": 4,
      "cloud_revision_id": "018fe9c0-0000-7000-8000-000000000099"
    }
  ]
}
```

单条 rejected 不影响同批其他合法事件：

```json
{
  "results": [
    {
      "event_id": "evt_bad",
      "entity_id": "backup_jobs:job_001",
      "revision_id": "",
      "status": "rejected",
      "reason": "revision_id is required"
    }
  ]
}
```

请求级错误：

| HTTP | `error` | 触发条件 |
| --- | --- | --- |
| 400 | `invalid_json` | 请求体不是合法 JSON 或包含未知字段。 |
| 400 | `empty_events` | `events` 为空。 |
| 400 | `too_many_events` | 单次超过 100 条。 |
| 503 | `retryable_error` | PostgreSQL 写入不可用。 |

#### `GET /v1/reconcile/entities/{entity_id}`

成功 `200`：

```json
{
  "entity_id": "backup_jobs:job_001",
  "entity_type": "backup_jobs",
  "data_version": 3,
  "revision_id": "018fe9c0-0000-7000-8000-000000000001",
  "canonical_record_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "updated_by_device_id": "dev_01234567-89ab-cdef-0123-456789abcdef",
  "recent_revisions": [
    {
      "event_id": "evt_111",
      "revision_id": "018fe9c0-0000-7000-8000-000000000001",
      "data_version": 3,
      "apply_status": "synced",
      "canonical_record_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "created_at": "2026-06-17T08:00:01Z"
    }
  ]
}
```

典型错误：`400 invalid_entity_id`、`404 not_found`、`503 retryable_error`。

#### `GET /v1/contents/{content_id}`

成功 `200`：

```json
{
  "content_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "file_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "size_bytes": 1048576,
  "latest_entity_id": "content_objects:aaaaaaaa",
  "updated_at": "2026-06-17T08:00:00Z"
}
```

投影规则：只有 `entity_type=content_objects` 的 revision payload 同时包含 `content_id`、`file_sha256` 和 `size_bytes` 时，云端才更新 `content_objects` 投影表。

#### `GET /v1/archives/{archive_sha256}`

成功 `200`：

```json
{
  "archive_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
  "archive_size": 8388608,
  "remote_path": "/apps/auto_backup_bdnetdesk/backups/2026/06/17/dev_x/job_1/archives/000001-cccc.7z",
  "remote_verified": true,
  "latest_entity_id": "archives:archive_001",
  "updated_at": "2026-06-17T08:00:00Z"
}
```

投影规则：`entity_type=archives` 或 `archive_objects` 的 payload 包含 `archive_sha256` 时更新 `archive_objects` 投影表；`archive_size` 可来自 `archive_size`、`size_bytes` 或 `size`。

#### `GET /v1/backups`

调用方式：

```http
GET /v1/backups?device_id=current&limit=5000
GET /v1/backups?device_id=all&limit=5000
```

约束：

- `device_id` 省略时等同 `current`。
- `device_id=current` 只返回当前认证设备历史。
- `device_id=all` 返回当前认证设备，以及与当前设备绑定到同一百度账号的其他设备历史；无共享账号时退化为当前设备历史。
- 显式传其他设备 ID 仍返回 `403 forbidden_device`，不得用任意设备 ID 枚举历史。
- 客户端默认使用 `all` 拉取跨设备历史；旧服务返回 `403 forbidden_device` 时必须回退到 `current`，但只能作为兼容路径。
- `limit` 范围 1-20000，默认 5000。
- 返回实体类型限定为 `backup_jobs`、`backup_sources`、`file_items`、`folder_items`、`content_objects`、`content_references`、`archives`、`archive_members`、`remote_objects`。

成功 `200`：

```json
{
  "device_id": "dev_01234567-89ab-cdef-0123-456789abcdef",
  "entities": [
    {
      "entity_id": "backup_jobs:job_001",
      "entity_type": "backup_jobs",
      "data_version": 3,
      "revision_id": "018fe9c0-0000-7000-8000-000000000001",
      "canonical_record_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "updated_by_device_id": "dev_01234567-89ab-cdef-0123-456789abcdef",
      "payload": {}
    }
  ]
}
```

### 百度授权云端 API

| 方法 | 路径 | 认证 | 用途 |
| --- | --- | --- | --- |
| `POST` | `/v1/baidu/auth/sessions` | 是 | 创建百度授权 session。 |
| `GET` | `/v1/baidu/auth/sessions/{session_id}` | 是 | 查询授权 session 状态。 |
| `POST` | `/v1/baidu/auth/sessions/{session_id}/complete` | 是 | 用本机 wrapping key 完成 token 加密入库。 |
| `GET` | `/v1/baidu/oauth/callback` | 否 | 百度授权码回调记录入口。 |
| `GET` | `/v1/baidu/accounts` | 是 | 列出当前设备可用百度账号。 |
| `POST` | `/v1/baidu/accounts/{account_id}/select` | 是 | 选择当前设备使用的百度账号。 |
| `GET` | `/v1/baidu/accounts/{account_id}/token` | 是 | 读取当前设备该账号的密文 token。 |
| `PUT` | `/v1/baidu/accounts/{account_id}/token` | 是 | 刷新后按版本回写密文 token。 |
| `POST` | `/v1/baidu/accounts/{account_id}/refresh-lease` | 是 | 获取当前设备账号级 refresh 租约。 |

#### `POST /v1/baidu/auth/sessions`

请求：

```json
{
  "flow": "device_code",
  "encryption_method": "password_argon2id_aes256gcm_v1"
}
```

约束：

- `flow` 默认 `device_code`，可选 `device_code` 或 `authorization_code`。
- `encryption_method` 默认 `password_argon2id_aes256gcm_v1`，可选 `password_argon2id_aes256gcm_v1` 或 `rsa_oaep_sha256_aes256gcm_v1`。
- RSA 模式必须提供 `rsa_public_key_pem`。

成功 `201`：

```json
{
  "session_id": "bauth_xxx",
  "flow": "device_code",
  "status": "pending",
  "scope": "basic,netdisk",
  "encryption_method": "password_argon2id_aes256gcm_v1",
  "user_code": "ABCD-EFGH",
  "verification_url": "https://openapi.baidu.com/device",
  "qrcode_url": "https://...",
  "expires_at": "2026-06-17T08:10:00Z"
}
```

典型错误：`400 invalid_flow`、`400 invalid_encryption`、`502 baidu_oauth_unavailable`、`503 retryable_error`。

#### `GET /v1/baidu/auth/sessions/{session_id}`

成功 `200`：

```json
{
  "session_id": "bauth_xxx",
  "flow": "device_code",
  "status": "authorized",
  "scope": "basic,netdisk",
  "encryption_method": "password_argon2id_aes256gcm_v1",
  "expires_at": "2026-06-17T08:10:00Z"
}
```

状态枚举：`pending`、`authorized`、`completed`、`failed`、`expired`。

#### `POST /v1/baidu/auth/sessions/{session_id}/complete`

password 模式请求：

```json
{
  "wrapping_key_base64": "base64url-no-padding-32-byte-key"
}
```

RSA 模式请求：

```json
{
  "rsa_public_key_pem": "-----BEGIN PUBLIC KEY-----...",
  "private_key_hint": "local-key-2026"
}
```

成功 `200`：

```json
{
  "session": {
    "session_id": "bauth_xxx",
    "flow": "device_code",
    "status": "completed",
    "scope": "basic,netdisk",
    "encryption_method": "password_argon2id_aes256gcm_v1",
    "expires_at": "2026-06-17T08:10:00Z",
    "account_id": "bacc_xxx"
  },
  "account": {
    "account_id": "bacc_xxx",
    "device_id": "dev_01234567-89ab-cdef-0123-456789abcdef",
    "display_name": "百度用户",
    "baidu_uid": "123456",
    "scope": "basic,netdisk",
    "token_expires_at": "2026-07-17T08:00:00Z",
    "token_valid": true,
    "encryption_method": "password_argon2id_aes256gcm_v1",
    "token_version": 1,
    "selected": true,
    "current_device": true,
    "last_verify_status": "valid"
  },
  "token": {
    "account_id": "bacc_xxx",
    "encryption_method": "password_argon2id_aes256gcm_v1",
    "token_version": 1,
    "token_expires_at": "2026-07-17T08:00:00Z",
    "encrypted_token_json": {
      "version": 1,
      "ciphertext": "..."
    }
  }
}
```

安全要求：响应包含密文 envelope，但不得在 UI、日志或测试快照中输出 `encrypted_token_json` 正文。

典型错误：

| HTTP | `error` | 触发条件 |
| --- | --- | --- |
| 404 | `not_found` | session 不存在。 |
| 409 | `authorization_pending` | 百度授权尚未完成。 |
| 410 | `session_expired` | session 过期。 |
| 502 | `baidu_userinfo_unavailable` | token 换取后读取百度用户信息失败。 |

#### `GET /v1/baidu/oauth/callback`

调用方式：

```http
GET /v1/baidu/oauth/callback?state=bstate_xxx&code=auth_code
```

成功 `200`：

```json
{
  "status": "authorized",
  "session_id": "bauth_xxx",
  "message": "baidu authorization callback recorded; return to the client to finish encryption"
}
```

该接口只记录百度返回的 `code/state/error`；最终 token 交换、加密和入库仍由已认证客户端调用 `complete`。

#### `GET /v1/baidu/accounts`

成功 `200`：

```json
{
  "accounts": [
    {
      "account_id": "bacc_xxx",
      "device_id": "dev_01234567-89ab-cdef-0123-456789abcdef",
      "display_name": "百度用户",
      "baidu_uid": "123456",
      "baidu_uk": "654321",
      "scope": "basic,netdisk",
      "token_expires_at": "2026-07-17T08:00:00Z",
      "token_valid": true,
      "encryption_method": "password_argon2id_aes256gcm_v1",
      "token_version": 1,
      "selected": true,
      "current_device": true,
      "last_verify_status": "valid"
    }
  ]
}
```

#### `POST /v1/baidu/accounts/{account_id}/select`

请求体为空对象：

```json
{}
```

成功返回单个 `BaiduAccountResponse`。不存在返回 `404 not_found`。

#### `GET /v1/baidu/accounts/{account_id}/token`

成功 `200`：

```json
{
  "account_id": "bacc_xxx",
  "encryption_method": "password_argon2id_aes256gcm_v1",
  "token_version": 1,
  "token_expires_at": "2026-07-17T08:00:00Z",
  "encrypted_token_json": {
    "version": 1,
    "ciphertext": "..."
  }
}
```

仅返回当前设备绑定的密文 token。服务端不提供明文 token 解密接口。

#### `PUT /v1/baidu/accounts/{account_id}/token`

请求：

```json
{
  "expected_token_version": 1,
  "token_expires_at": "2026-07-17T08:00:00Z",
  "encryption_method": "password_argon2id_aes256gcm_v1",
  "encrypted_token_json": {
    "version": 1,
    "ciphertext": "..."
  },
  "last_verify_status": "valid"
}
```

成功 `200` 返回更新后的 `BaiduEncryptedToken`，`token_version` 递增。

典型错误：`400 invalid_token_version`、`400 invalid_token`、`404 not_found`、`409 token_version_conflict`。

#### `POST /v1/baidu/accounts/{account_id}/refresh-lease`

请求：

```json
{
  "lease_id": "blease_client_optional",
  "duration_seconds": 300
}
```

约束：`duration_seconds` 自动夹在 30-900 秒之间；`lease_id` 为空时云端生成。

成功获取 `200`：

```json
{
  "acquired": true,
  "account_id": "bacc_xxx",
  "lease_id": "blease_xxx",
  "holder_device_id": "dev_01234567-89ab-cdef-0123-456789abcdef",
  "expires_at": "2026-06-17T08:05:00Z"
}
```

被其他未过期租约占用 `409`：

```json
{
  "acquired": false,
  "account_id": "bacc_xxx",
  "lease_id": "blease_other",
  "holder_device_id": "dev_other",
  "expires_at": "2026-06-17T08:05:00Z"
}
```

## pywebview Bridge API

### 前端调用方式

前端只能通过 `client/src/auto_backup_client/webui/js/api.js` 调用 bridge：

```js
const data = await call("list_jobs");
```

`call()` 会等待 `pywebviewready`，20 秒超时，并要求 bridge 返回 `ok: true`。失败时只把 `error.message` 抛给 UI。

成功包络：

```json
{
  "ok": true,
  "data": {
    "jobs": []
  }
}
```

失败包络：

```json
{
  "ok": false,
  "error": {
    "type": "ValueError",
    "message": "请至少选择一个备份来源"
  }
}
```

写操作必须由 bridge 内部 `_write_lock` 串行化，并在执行前确认真实 `device_id` 已解析；否则返回“设备凭据未就绪”错误。

### Operation DTO

长操作返回：

```json
{
  "operation": {
    "operation_id": "4d3c2b1a...",
    "operation_id_hint": "4d3c2b1a...88cc21",
    "kind": "backup",
    "kind_label": "备份任务",
    "status": "running",
    "stage": "backup",
    "message": "正在执行扫描、归档、上传与同步",
    "progress": 0.32,
    "context": {
      "job_id": "job_001",
      "job_id_hint": "job_001",
      "job_name": "照片备份",
      "job_status": "running",
      "job_status_label": "运行中",
      "source_count": 2,
      "target_label": "照片备份"
    },
    "created_at": "2026-06-17T08:00:00+00:00",
    "updated_at": "2026-06-17T08:00:03+00:00",
    "started_at": "2026-06-17T08:00:00+00:00",
    "finished_at": null,
    "cancel_requested": false
  }
}
```

状态枚举：`pending`、`running`、`completed`、`failed`、`canceling`。前端旧文档中的 `succeeded/canceled` 不是当前真实 bridge 状态。

`kind` 当前包括：`backup`、`remote_reconcile`、`remote_repair`、`cleanup`、`restore`。

字段约束：

- `kind_label` 面向用户展示，当前包括“备份任务”“恢复”“原始数据清理”“远端校对”“远端修复”。
- `context` 必须只包含脱敏上下文，用于解释“这次操作作用在哪个任务或候选上”；可包含 `job_id/job_id_hint/job_name/job_status_label/source_count/selection_count/source_names/target_label`。
- 操作失败时 `error.message` 必须给出脱敏后的具体原因，不能只显示“详细信息已脱敏”；`error` 可包含 `stage/status_code/code/next_step` 等非敏感诊断字段。
- 前端最近操作必须展示 `kind_label`、`context.target_label` 或任务名、阶段、失败原因和 `operation_id_hint`，不能只显示“操作失败”。
- `context.source_names` 只允许来源显示名，不得包含完整本地路径。

### Bridge 方法表

| 方法 | 参数 | 返回 | 写锁/长操作 | 备注 |
| --- | --- | --- | --- | --- |
| `get_app_state()` | 无 | `app`、`dashboard`、`settings` | 否 | 启动页总状态。 |
| `list_jobs()` | 无 | `jobs[]` | 否 | 最近 100 个任务，DTO 必须区分本机任务和其他设备历史任务。 |
| `list_job_choices()` | 无 | `jobs[]` | 否 | 恢复、清理、校对页的任务下拉，复用任务 DTO 的脱敏字段。 |
| `create_job(name, sources)` | `name` string；`sources[]` | `job` | 写锁 | `sources` 可来自选择器 token 或拖拽 path。 |
| `start_job(job_id, passwords, options)` | `passwords.archive_password`、`passwords.authorization_password`；上传选项 | `operation` | 长操作 | 运行备份 pipeline。 |
| `transition_job(job_id, action)` | `action=pause/resume/cancel` | `job` | 写锁 | `resume` 映射为 `running`。 |
| `choose_sources(kind="file")` | `file/directory/mixed` | `sources[]` | 否 | `mixed` 先选文件再选目录，返回 `source_token`。 |
| `choose_directory(purpose="general")` | `purpose` string | `directory` | 否 | 目前不按 purpose 改变对话框行为。 |
| `list_baidu_accounts()` | 无 | `accounts[]`、`selected_account_id` | 否 | 只返回摘要和本机标记。 |
| `start_baidu_authorization()` | 无 | `authorization` | 写锁 | 创建 device code session，并保存内部 session ID。 |
| `poll_baidu_authorization()` | 无 | `authorization` | 写锁 | 当前真实签名无 `session_id` 参数。 |
| `complete_baidu_authorization(password)` | 授权密码 | `account` | 写锁 | 使用内部 session ID。提交后前端必须清空密码。 |
| `select_baidu_account(account_id)` | 账号 ID | `account` | 写锁 | 选择当前设备账号。 |
| `verify_baidu_token(account_id, password)` | 账号 ID、授权密码 | `verification` | 否 | 失败也返回 `valid=false` 的成功包络。 |
| `list_source_mappings(filter)` | `job_id`、`keyword`、`limit` | `summary`、`rows[]` | 否 | 调用前会尝试拉取云端历史。 |
| `run_remote_reconcile(scope)` | `account_id`、`authorization_password`、`job_id/upload_session_id/remote_dir` | `operation` | 长操作 | 需要真实百度 `list/listall`。 |
| `apply_remote_repairs(selection, confirmation)` | 筛选、确认词 | `operation` | 长操作 | 确认词为 `APPLY_REMOTE_REPAIR`。 |
| `list_cleanup_candidates(filter)` | `job_id`、`keyword`、`limit` | `summary`、`candidates[]` | 否 | 返回确认词。 |
| `apply_cleanup(selection, options)` | `content_reference_id[]`、清理选项 | `operation` | 长操作 | 空选择不得解释为全部。 |
| `list_restore_candidates(filter)` | `job_id`、`keyword`、`limit` | `summary`、`candidates[]` | 否 | 来源级候选。 |
| `apply_restore(selection, options)` | `content_reference_id[]`、恢复选项 | `operation` | 长操作 | 默认 `keep_both`。 |
| `get_operation(operation_id)` | operation ID | `operation` | 否 | 轮询长操作。 |
| `cancel_operation(operation_id)` | operation ID | `operation` | 否 | 仅标记取消请求。 |
| `get_cloud_sync_summary(entity_id)` | entity ID | `summary` | 否 | 回读云端 summary，DTO 必须对齐 `EntitySummary` 模型。 |

### 关键 DTO 示例

`get_app_state()` 摘要：

```json
{
  "app": {
    "name": "Auto Backup BD Netdisk",
    "version": "desktop",
    "device_id_hint": "dev_01234567...cdef",
    "cloud_api_base_url": "https://backup.baichengedu.com",
    "device_credential_source": "dpapi",
    "device_token_available": true,
    "device_id_resolved": true
  },
  "dashboard": {
    "jobs": [],
    "status_counts": {},
    "risks": [],
    "operations": [],
    "accounts": {
      "available": true,
      "selected_account_id": "bacc_xxx",
      "items": []
    }
  }
}
```

`choose_sources("mixed")`：

```json
{
  "sources": [
    {
      "source_token": "32f0f0...",
      "source_type": "directory",
      "display_name": "Photos",
      "path_digest": "0123456789abcdef"
    }
  ]
}
```

`list_jobs()` 任务 DTO：

```json
{
  "jobs": [
    {
      "job_id": "job_001",
      "job_id_hint": "job_001",
      "entity_id": "backup_job_job_001",
      "name": "照片备份",
      "status": "running",
      "status_label": "运行中",
      "scope": "local",
      "scope_label": "本机任务",
      "owner_device_hint": "dev_01234567...cdef",
      "device_group_label": "本机任务",
      "current_device": true,
      "imported_from_cloud": false,
      "source_count": 2,
      "local_source_count": 2,
      "can_start": false,
      "can_continue": true,
      "can_pause": true,
      "can_cancel": true,
      "last_stage": "upload",
      "last_error": "",
      "sync_status": "sync_pending",
      "updated_at": "2026-06-22T08:00:00Z"
    }
  ]
}
```

字段约束：

- `scope=local` 表示 `backup_jobs.device_id` 等于本机真实 `device_id`，可按 `can_*` 字段提供开始、继续、暂停和取消入口。
- `scope=global` 表示其他设备或全局云端历史任务，只允许只读展示，不得在本机继续、暂停或取消；恢复/校对页面可基于已导入索引读取候选。
- `device_group_label` 用于前端把其他设备任务按设备摘要分组展示。
- `last_error` 必须使用脱敏后的可读原因；不得包含完整本地路径、完整远端路径、Device Token、百度 token、授权密码、归档密码或 wrapping key。
- `source_count` 优先使用 `backup_jobs.source_count` 持久化值；云端历史导入尚未包含 `backup_sources` 行时，不能因为本地来源行数为 0 而显示 `0 个来源`。
- `local_source_count` 表示当前 SQLite 中已导入的 `backup_sources` 行数，仅用于诊断。
- 本机 `queued/running/paused/failed_retryable` 任务可 `can_continue=true`；继续任务仍需前端页面内一次性提交运行时密码，不得使用 `window.prompt`。

`start_job(...)` 参数：

```json
{
  "job_id": "job_xxx",
  "passwords": {
    "archive_password": "runtime-only",
    "authorization_password": "runtime-only"
  },
  "options": {
    "account_id": "bacc_xxx",
    "root_dir": "/apps/auto_backup_bdnetdesk/backups",
    "run_upload": true,
    "check_quota": true,
    "sync_outbox": true,
    "reconcile_remote": true,
    "enforce_cache_budget": false,
    "max_archive_size_bytes": 4294967296,
    "part_size": 4194304,
    "cleanup_cache_artifacts": false
  }
}
```

`verify_baidu_token(...)` 成功：

```json
{
  "verification": {
    "account_id": "bacc_xxx",
    "valid": true,
    "message": "百度 token 可解密",
    "token_version": 1,
    "token_expires_at": "2026-07-17T08:00:00+00:00"
  }
}
```

`apply_cleanup(...)` 参数：

```json
{
  "selection": ["cref_xxx"],
  "options": {
    "method": "recycle_bin",
    "dry_run": false,
    "confirm_text": "CLEAN_ORIGINALS",
    "advanced_enabled": false,
    "permanent_confirm_text": ""
  }
}
```

清理页 UI 约束：必须先通过 `list_job_choices()` 让用户按任务筛选候选；候选表必须展示任务名、来源/文件显示名、清理状态、待同步提示和阻塞原因。空选择不得解释为全部。

`apply_restore(...)` 参数：

```json
{
  "selection": ["cref_xxx"],
  "options": {
    "archive_password": "runtime-only",
    "target_mode": "manual_root",
    "target_root": "D:\\RestoreTarget",
    "conflict_strategy": "keep_both",
    "account_id": "bacc_xxx",
    "authorization_password": "runtime-only"
  }
}
```

恢复页 UI 约束：必须先通过 `list_job_choices()` 让用户按任务筛选来源级恢复候选；候选表必须展示任务名、来源显示名、来源类型、文件数、本地 archive 是否可用、远端 archive 是否确认和阻塞原因。恢复操作提交后 operation 必须显示关联任务或选择数量。

校对页 UI 约束：必须提供任务下拉；选择任务后，来源映射按该任务过滤，远端校对默认使用该 `job_id`，Cloud Sync 回读默认填入该任务的 `entity_id`。`upload_session_id` 和 `remote_dir` 仍可作为排障输入，但不得成为普通用户唯一入口。

`get_cloud_sync_summary(...)` 契约返回：

```json
{
  "summary": {
    "entity_id": "backup_jobs:job_001",
    "entity_type": "backup_jobs",
    "data_version": 3,
    "revision_id": "018fe9c0-0000-7000-8000-000000000001",
    "revision_id_hint": "018f...0001",
    "canonical_record_sha256": "0123456789abcdef",
    "canonical_record_sha256_hint": "0123456789abcdef",
    "updated_by_device_id": "dev_01234567-89ab-cdef-0123-456789abcdef",
    "updated_by_device_hint": "dev_01234567...cdef",
    "recent_revisions": [
      {
        "event_id": "evt_111",
        "event_id_hint": "evt_..._111",
        "revision_id": "018fe9c0-0000-7000-8000-000000000001",
        "revision_id_hint": "018f...0001",
        "data_version": 3,
        "apply_status": "synced",
        "canonical_record_sha256": "0123456789abcdef",
        "canonical_record_sha256_hint": "0123456789abcdef",
        "created_at": "2026-06-17T08:00:01+00:00"
      }
    ]
  }
}
```

`*_hint` 字段是 bridge 为前端脱敏展示额外提供的摘要字段；完整 `revision_id`、`canonical_record_sha256` 和 `updated_by_device_id` 仍保留在 DTO 中，供 R13 真实回读校验和开发诊断使用。

2026-06-22 校对记录：`webview_bridge.py#get_cloud_sync_summary` 已按上方契约对齐 `baidu.models.EntitySummary`，并在校对与同步页补齐按实体 ID 回读 Cloud Sync summary 的入口。干净 Windows R13 仍需用真实云端 summary 回读和 duplicate 结果复验。

## 百度网盘 API 调用契约

本项目客户端本地解密百度 access token 后直接调用百度官方 API。所有请求必须设置 `User-Agent: pan.baidu.com`。百度返回中 `errno=0` 或 `error_code=0` 表示成功；HTTP 4xx/5xx 或非零错误码必须转为脱敏 `BaiduNetdiskError`。

| 步骤 | 方法与路径 | 关键参数 | 成功字段 | 本项目用途 |
| --- | --- | --- | --- | --- |
| 容量 | `GET https://pan.baidu.com/api/quota` | `access_token`、`checkfree=1`、`checkexpire=1` | `errno=0`、`total/used/free/expire` | 上传前容量检查。 |
| 用户信息探针 | `GET /rest/2.0/xpan/nas?method=iotqueryuinfo` | `access_token`、`device_id` | `error_code=0`、`data` | 可选探针；真实差异中不能阻塞上传。 |
| 预上传 | `POST /rest/2.0/xpan/file?method=precreate` | `path`、`size`、`isdir=0`、`autoinit=1`、`rtype=0`、`block_list`、`content-md5`、`slice-md5` | `errno=0`、`uploadid`、`block_list` | 获取 uploadid 和待上传分片。 |
| 获取上传域名 | `GET https://d.pcs.baidu.com/rest/2.0/pcs/file?method=locateupload` | `appid=250528`、`path`、`uploadid`、`upload_version=2.0` | `error_code=0`、`servers[]` | 选择 HTTPS 上传服务器。 |
| 分片上传 | `POST {upload_server}/rest/2.0/pcs/superfile2?method=upload` | `type=tmpfile`、`path`、`uploadid`、`partseq`、multipart `file` | `errno=0`、`md5` | 上传缺失分片并校验分片 MD5。 |
| 创建文件 | `POST /rest/2.0/xpan/file?method=create` | `path`、`size`、`isdir=0`、`uploadid`、`rtype=0`、`block_list` | `errno=0`、`fs_id`、`md5`、`path` | 合并分片为最终对象。 |
| 非递归列表 | `GET /rest/2.0/xpan/file?method=list` | `dir`、`start`、`limit`、`order`、`desc`、`web` | `errno=0`、`list[]` | 任务目录或 archive 目录校对。 |
| 递归列表 | `GET /rest/2.0/xpan/multimedia?method=listall` | `path`、`recursion`、`start`、`limit` | `errno=0`、`list[]`、`has_more`、`cursor` | 远端递归校对；频率按每分钟不超过 8 次。 |
| 文件元信息 | `GET /rest/2.0/xpan/multimedia?method=filemetas` | `fsids=[...]`、`dlink=1` | `errno=0`、`list[].dlink` | 恢复下载前获取 dlink。 |
| dlink 下载 | `GET {dlink}&access_token=...` | `User-Agent: pan.baidu.com` | HTTP 2xx/302 后字节流 | 下载缺失 archive。 |
| 删除测试文件 | `POST /rest/2.0/xpan/file?method=filemanager&opera=delete` | `async=0`、`filelist=[...]` | `errno=0`、`info[]` | 仅真实联调清理本批测试远端对象。 |

上传固定顺序：

```text
quota -> precreate -> locateupload -> superfile2(partseq...) -> create -> upload .meta.json -> upload job.index.json -> list/listall reconcile
```

远端路径固定：

```text
/apps/{appname}/backups/{yyyy}/{MM}/{dd}/{device_id}/{job_id}/archives/{archive_seq}-{archive_sha256}.7z
/apps/{appname}/backups/{yyyy}/{MM}/{dd}/{device_id}/{job_id}/archives/{archive_seq}-{archive_sha256}.meta.json
/apps/{appname}/backups/{yyyy}/{MM}/{dd}/{device_id}/{job_id}/job.index.json
```

不得使用 `archive_sha256` 作为远端目录分桶。`rtype` 固定为 `0`，遇到冲突不得让百度自动重命名。

典型百度错误处理：

```json
{
  "errno": -8,
  "errmsg": "file already exists"
}
```

处理规则：保留百度错误码和脱敏 message 到业务错误摘要；不得输出 access token、完整本地路径、完整远端路径或原始响应中的敏感字段。

## 本地 SQLite 数据库

### 迁移与事务

- 迁移目录：`client/migrations/sqlite/`。
- 应用记录：`schema_migrations(migration_name, migration_sha256, applied_at)`。
- 迁移一旦应用，文件 SHA256 不得被修改；修改已应用迁移会触发运行时错误。
- `SQLiteClientStore.transaction()` 使用 `BEGIN IMMEDIATE`；业务表和 `sync_outbox` 必须同事务写入。

### 同步实体字段

同步实体表必须携带：

| 字段 | 含义 |
| --- | --- |
| `entity_id` | 云端实体 ID，表内唯一。 |
| `schema_version` | 本地 payload schema 版本，当前 `1`。 |
| `data_version` | 单实体单调递增版本。 |
| `revision_id` | UUIDv7 字符串。 |
| `updated_at` | UTC ISO 时间。 |
| `updated_by_device_id` | 写入设备 ID。 |
| `sync_status` | 本地同步状态。 |
| `deleted_at` | 软删除时间，可为空。 |
| `canonical_record_sha256` | 排除控制/本地敏感字段后的稳定 JSON SHA256。 |
| `last_synced_revision_id` | 最近成功同步的 revision。 |

`sync_status` 枚举：

```text
local_committed, sync_pending, syncing, synced, sync_conflict, sync_failed_retryable
```

### `sync_outbox`

| 字段 | 约束 |
| --- | --- |
| `event_id` | 主键，`evt_` 前缀。 |
| `entity_type` | 业务表名。 |
| `entity_id` | 云端实体 ID。 |
| `revision_id` | 业务表 revision。 |
| `operation` | `upsert/delete`。 |
| `payload_json` | 已过滤本地敏感字段后的 JSON。 |
| `status` | `pending/syncing/synced/sync_conflict/retryable/failed_terminal`。 |
| `retry_count` | 重试次数。 |
| `next_retry_at` | 下次重试时间。 |
| `last_error` | 脱敏错误。 |
| `UNIQUE(entity_id, revision_id)` | 防止同一 revision 重复入队。 |

状态转换：

```text
pending -> syncing -> synced
pending/syncing -> retryable -> syncing
syncing -> sync_conflict
syncing -> failed_terminal
```

云端返回处理：

| 云端状态 | 本地 outbox | 业务表 `sync_status` |
| --- | --- | --- |
| `synced` | `synced` | `synced` |
| `duplicate` | `synced` | `synced` |
| `conflict` | `sync_conflict` | `sync_conflict` |
| `rejected` | `failed_terminal` | 保持或人工处理 |
| 网络/5xx | `retryable` | `sync_failed_retryable` |

### SQLite 表清单

| 表 | 主键/唯一约束 | 同步 | 写入责任 | 说明 |
| --- | --- | --- | --- | --- |
| `sync_outbox` | `event_id`；`UNIQUE(entity_id, revision_id)` | 否，传输队列 | `SQLiteClientStore.enqueue_revision` | 本地到云端 revision 队列。 |
| `upload_sessions` | `upload_session_id`；`entity_id` 唯一；`remote_archive_path` 唯一 | 是 | `BaiduResumableUploader`/pipeline | archive 上传总账本，含 `uploadid` 本地字段。 |
| `upload_parts` | `upload_part_id`；`entity_id` 唯一；`UNIQUE(upload_session_id, partseq)` | 是 | 上传器 | 分片状态与 MD5。 |
| `remote_objects` | `remote_object_id`；`entity_id` 唯一；`remote_path` 唯一 | 是 | 上传器/校对修复 | 百度远端 archive、meta、job index 账本。 |
| `backup_jobs` | `backup_job_id`；`entity_id` 唯一 | 是 | `BackupJobManager`/pipeline | 任务状态主表。 |
| `backup_sources` | `backup_source_id`；`UNIQUE(backup_job_id, local_path)`；`entity_id` 后续唯一 | 是 | `BackupJobManager` | 用户选择来源，本地路径不得进云端 payload。 |
| `file_items` | `file_item_id`；`entity_id` 唯一；`UNIQUE(backup_job_id, backup_source_id, relative_path)` | 是 | 扫描器 | 文件扫描、完整 hash、content_id。 |
| `folder_items` | `folder_item_id`；`entity_id` 唯一；`UNIQUE(backup_job_id, backup_source_id, relative_path)` | 是 | 扫描器 | 文件夹 manifest hash。 |
| `scan_issues` | `scan_issue_id` | 否 | 扫描器 | 不可读、跳过链接等本地问题。 |
| `content_objects` | `content_id`；`entity_id` 唯一；`UNIQUE(file_sha256, size_bytes)` | 是 | 去重索引 | 最终内容去重对象。 |
| `content_references` | `content_reference_id`；`entity_id` 唯一；`file_item_id` 唯一 | 是 | 去重/归档/清理/恢复 | 来源文件到内容对象引用。 |
| `archives` | `archive_id`；`entity_id` 唯一；`UNIQUE(job_id, archive_seq)`；`archive_sha256` 唯一 | 是 | 归档器 | 7-Zip archive 与 manifest 摘要。 |
| `archive_members` | `archive_member_id`；`entity_id` 后续唯一 | 是 | 归档器 | manifest/payload/reference/folder 成员。 |
| `cache_artifacts` | `artifact_id`；`artifact_path` 唯一 | 否 | cache manager/归档/恢复 | 缓存生命周期，本地路径不得同步。 |
| `source_cleanup_records` | `source_cleanup_record_id`；`entity_id` 唯一 | 是 | 清理服务 | 原始数据清理审计记录。 |
| `restore_records` | `restore_record_id`；`entity_id` 唯一 | 是 | 恢复服务 | 恢复审计记录；目标路径只本地保存。 |
| `schema_migrations` | `migration_name` | 否 | migration runner | 本地迁移校验。 |

### 关键表字段边界

`backup_jobs`：

- 状态：`queued/running/paused/canceled/completed/failed_retryable/failed_terminal`。
- `last_stage` 和 `last_error` 用于 UI 解释卡点；`last_error` 必须脱敏。
- `source_count >= 1`。

`backup_sources`：

- `source_type=file/directory`。
- `local_path` 只保存在本地 SQLite。
- 同步 payload 必须过滤 `local_path`。

`file_items`：

- `md5` 32 位，`sha256` 64 位，`content_id` 64 位。
- `content_id = sha256("v1:file:" + size + ":" + file_sha256)` 的口径来自 spec。
- `file_volume_serial`、`file_index` 仅用于清理前身份复查。

`content_objects`：

- 唯一去重键是 `file_sha256 + size_bytes`。
- `reference_count`、`payload_reference_count`、`duplicate_reference_count` 必须非负。
- `cloud_candidate_status` 只允许 `not_checked/missing/cloud_duplicate_candidate/hash_mismatch/retryable_error`。

`archives`：

- `archive_type=payload/manifest_only/mixed`。
- `verify_status=not_started/standard_test_started/standard_test_passed/failed`。
- `strict_verify_status=not_requested/strict_extract_started/strict_extract_hash_checked/strict_extract_cleanup_done/failed`。
- 明文 manifest 只允许短暂存在 staging，验证后删除。

`upload_sessions`：

- `part_size >= 4194304`，`total_parts >= 1`。
- `upload_status=planned/precreated/uploading/parts_uploaded/remote_created/failed_retryable/failed_terminal`。
- `uploadid` 是本地续传字段，必须从 sync payload 过滤。

`remote_objects`：

- `object_type=archive/archive_meta/job_index`。
- `status=remote_created/remote_missing/remote_mismatch/deleted`。
- `remote_path` 在本地唯一；UI 只显示 digest 或摘要。

`source_cleanup_records`：

- `cleanup_method=recycle_bin/quarantine/permanent_delete`。
- `cleanup_status=requested/moved_to_recycle_bin/moved_to_quarantine/permanently_deleted/failed`。
- `original_path`、`quarantine_path` 只本地保存，云端 payload 必须过滤。

`restore_records`：

- `restore_target_mode=original_path/manual_path`。
- `conflict_strategy=keep_both/skip_existing/overwrite`；产品默认 `keep_both`，覆盖恢复仍需单独验收。
- `archive_source=local_cache/downloaded/not_available`。
- `target_path/final_path/archive_path` 只本地保存，云端 payload 必须过滤。

## 云端 PostgreSQL 数据库

### 迁移与 readiness

迁移目录：`cloud-api/migrations/postgres/`，已嵌入 Go 二进制。`cloud-api serve` 启动时自检并自动执行缺失迁移；`cloud-api migrate` 只作为排障入口。

`/v1/readyz` 必须同时检查 PostgreSQL 可连接和关键 schema。缺表/缺列返回 `schema_not_ready`。

### PostgreSQL 表清单

| 表 | 主键/唯一约束 | 写入来源 | 说明 |
| --- | --- | --- | --- |
| `devices` | `device_id`；`device_token_hash` 旧唯一 | `POST /devices/register` | 设备元数据和稳定 fingerprint。 |
| `device_tokens` | `device_token_hash` | `POST /devices/register` | 多 Device Token 支持；认证优先查此表。 |
| `cloud_entities` | `entity_id` | `POST /sync/revisions` | 每个实体当前投影。 |
| `entity_revisions` | `PRIMARY KEY(entity_id, revision_id)`；`event_id` 唯一 | `POST /sync/revisions` | 所有 revision 历史和 apply 状态。 |
| `content_objects` | `content_id` | content revision 投影 | 云端内容去重查询投影。 |
| `archive_objects` | `archive_sha256` | archive revision 投影 | 云端归档查询投影。 |
| `baidu_accounts` | `account_id`；`baidu_uid` 唯一 | 百度授权 complete | 百度账号非设备级基础信息。 |
| `baidu_auth_sessions` | `session_id`；`state` 唯一 | 授权 session API/callback | 授权流程状态。 |
| `baidu_account_device_bindings` | `PRIMARY KEY(account_id, device_id)` | 账号选择/complete/token update | 当前设备级密文 token 与选择状态。 |
| `baidu_token_refresh_leases` | `account_id` | 旧账号级租约 | 历史兼容，当前设备级租约优先。 |
| `baidu_device_token_refresh_leases` | `PRIMARY KEY(account_id, device_id)` | refresh lease API | 当前设备账号级 refresh 租约。 |

### Cloud Sync 冲突规则

云端按 `entity_id` 加 PostgreSQL advisory lock。

幂等判断：

- 已存在相同 `event_id`：返回 `duplicate`。
- 已存在相同 `entity_id + revision_id`：若原状态 `synced` 则返回 `duplicate`，否则返回原状态。

冲突判断：

- 云端当前 `data_version > event.data_version`：冲突。
- 云端当前 `data_version == event.data_version` 且 `revision_id` 不同且 `canonical_record_sha256` 不同：冲突。

非冲突 upsert：

- 写入 `entity_revisions(apply_status='synced')`。
- upsert `cloud_entities`。
- 如 payload 可抽取内容或归档索引，同时 upsert `content_objects` 或 `archive_objects`。

### 百度授权表边界

`baidu_accounts` 保存百度账号级信息，但密文 token 当前以设备绑定为准。`baidu_account_device_bindings` 包含：

- `token_expires_at`
- `encryption_method`
- `encrypted_token_json`
- `private_key_hint`
- `token_version`
- `last_verified_at`
- `last_verify_status`
- `updated_at`

后续不得把新 token 只写回 `baidu_accounts` 而忽略设备绑定表，否则多设备授权会互相覆盖或读不到当前设备 token。

`baidu_device_token_refresh_leases` 以 `account_id + device_id` 为主键，避免同一账号不同设备的 refresh 租约互相污染。

## 变更前检查清单

修改云端 HTTP API 前：

- 更新本文对应接口的路径、方法、认证、请求、成功、错误和示例。
- 更新 Python `BaiduCloudClient` 模型和测试。
- 如响应字段变更，同步 pywebview bridge DTO 和前端渲染。
- 如涉及外部 API，按 `AGENTS.MD` 重新获取官方文档或记录获取失败。

修改 bridge API 前：

- 更新本文 Bridge 方法表和示例。
- 更新 `client/docs/frontend_spec_pywebview.md`，清理旧签名。
- 更新前端 `api.js` 调用方和测试。
- 确认敏感字段不进入 DTO。

修改 SQLite schema 前：

- 新增 migration，不改已应用 migration。
- 更新本文 SQLite 表清单和字段边界。
- 确认同步实体写入与 `sync_outbox` 同事务。
- 更新 `LOCAL_ONLY_SYNC_FIELDS`、`SYNC_ENTITY_TABLES` 或 canonical hash 规则时，必须在本文记录。

修改 PostgreSQL schema 前：

- 新增 migration，并确认嵌入二进制。
- 更新本文 PostgreSQL 表清单和 readiness 说明。
- 更新 `CheckSchema` 必要表/列检查。
- 更新部署文档和真实云端联调记录。

修改百度网盘 API 调用前：

- 优先获取官方文档；失败则记录尝试过程和依据。
- 更新本文百度 API 表。
- 真实上传/下载/删除/校对契约必须走真实百度 API 验收。
- 不得用 mock API 作为契约验收依据。
