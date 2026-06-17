# Tech：技术栈与工程边界

## 技术栈

| 层 | 技术 | 固定口径 |
| --- | --- | --- |
| 桌面客户端 | Python 3.12 + pywebview | Windows 使用 WebView2；不再使用 PySide6 UI。 |
| 前端 | 原生 HTML/CSS/JS | 不引入 React、Vue、Vite、Node 构建链。 |
| 本地数据库 | SQLite | 任务执行主库，业务写入与 outbox 同事务。 |
| 云端服务 | Go 1.25 + chi + pgx/pgxpool | 单二进制，启动自检并执行内置 PostgreSQL 迁移。 |
| 云端数据库 | PostgreSQL | 接收 revision 投影，支持最终一致和回读校验。 |
| HTTP 客户端 | httpx | 客户端访问云端和百度 API。 |
| 百度网盘 | 官方开放平台 | 固定使用 precreate -> superfile2 -> create。 |
| 加密压缩 | 7-Zip AES-256 | 必须使用真实 7-Zip 验证关键归档/恢复场景。 |
| 发布 | PyInstaller onedir | 入口为脚本路径，打包 webui 和 SQLite migrations。 |

## 目录边界

- Python 客户端源码、测试、迁移和客户端文档放在 `client/`。
- Go 云端服务放在 `cloud-api/`。
- 当前权威开发文档放在 `docs/current/`。
- 旧文档索引放在 `docs/legacy/README.md`。
- 构建产物、缓存、虚拟环境、本地 SQLite、日志和敏感配置不得提交。

## API 与数据库契约

`tech.md` 只描述架构边界和技术原则；具体接口、参数、成功/错误返回、示例、数据库表结构、同步 payload 和版本字段以 `docs/current/api_database_contract.md` 为准。

后续任何变更只要触及以下内容，必须先更新契约文档，再改代码：

- Go 云端 `/v1` HTTP API。
- pywebview `window.pywebview.api` bridge API。
- 百度网盘官方 API 调用参数、顺序、错误处理或真实联调口径。
- SQLite migration、同步实体字段、`sync_outbox` payload 或 canonical hash 过滤字段。
- PostgreSQL migration、schema readiness、Cloud Sync 投影或百度授权表。

如果契约文档与代码不一致，新增功能不得继续基于猜测开发；必须先在 `audit.md` 和 `document_change_audit.md` 记录偏差，再修正文档或代码。

## 客户端架构

客户端核心模块：

- `webview_app.py`：创建 pywebview 主窗口。
- `webview_bridge.py`：提供 `window.pywebview.api`，封装备份、授权、校对、清理、恢复和 operation registry。
- `sqlite_store.py`：SQLite 迁移、事务和 `sync_outbox` 同事务写入。
- `backup_jobs.py`：任务和来源模型。
- `scan_fingerprints.py`：扫描和指纹。
- `dedupe_index.py`：内容级去重索引。
- `archive_packager.py`：manifest 和 7-Zip 归档。
- `backup_pipeline.py`：端到端备份编排。
- `baidu/*`：授权、token、上传、校对和真实联调。
- `source_cleanup.py`：原始数据清理。
- `restore_flow.py`：来源级恢复。
- `sync_worker.py`：本地 outbox 到云端 revision 同步。

写操作必须进入 bridge 写锁或底层事务边界。UI 不直接操作数据库。

## 云端架构

Go 服务职责：

- 设备注册和 Device Token 认证。
- Cloud Sync revision 接收、幂等写入、冲突检测和 summary 查询。
- 百度授权 session、OAuth 回调、密文 token 存取、账号选择和刷新租约。
- 设备级备份历史查询。
- PostgreSQL schema readiness 和启动自迁移。

Go 服务不负责：

- 解密百度 token。
- 保存用户明文密码。
- 客户端任务执行。
- 直接操作用户百度网盘文件内容。

## 数据一致性

本地 SQLite 是任务执行主库。只要本地事务落盘成功，任务即可继续。

同步流程：

1. 本地业务表写入成功。
2. 同一 SQLite 事务写入 `sync_outbox`。
3. 后台同步器读取 `pending/retryable` 事件。
4. 调用 `POST /v1/sync/revisions`。
5. 云端按 `entity_id + revision_id` 幂等写入。
6. 云端返回 `synced` 或 `duplicate` 后，本地标记已同步。
7. 云端返回 `conflict` 后，本地进入 `sync_conflict`。
8. 网络或服务错误只能进入重试，不能误标记 synced。

真实同步验收必须用 `GET /v1/reconcile/entities/{entity_id}` summary 回读匹配证明，不得只看本地 outbox 状态。

## 稳定设备身份

客户端必须基于本机固定特征派生稳定 `device_id` 和 `device_fingerprint_hash`。`client_version` 只作为元数据，不参与 `device_id` 生成。

规则：

- 同一设备不同 client 版本保持同一 `device_id`。
- 云端注册校验 `device_id` 与 fingerprint hash 匹配。
- 同一 `device_id` 重复注册保持幂等，但可签发新的 Device Token。
- 运行时只有 `CLOUD_API_DEVICE_TOKEN` 时，必须复用本机 store 中的 `device_id` 或从真实云端 current-device 回读。
- 不得用 `current-device`、`environment`、`unknown-device` 作为真实业务写路径设备 ID。

## 百度 API 边界

上传固定流程：

```text
precreate -> locateupload -> superfile2 -> create
```

远端校对使用 `list/listall`。下载恢复使用 `filemetas` 获取 dlink 后下载。

真实联调要求：

- 授权、账号选择、token 解密、上传、删除清理和同步契约必须走真实云端或真实百度 API。
- 自动化单元测试可以 mock 纯本地逻辑，但不能用 mock API 作为云端/百度契约验收。
- 百度上传测试必须生成临时测试文件真实上传，完成后用百度官方删除接口清理。

## 安全与敏感信息

不得提交或输出：

- `.env`。
- PostgreSQL DSN 或密码。
- Device Token。
- 百度 access token / refresh token。
- 授权密码、归档密码、wrapping key。
- 本地 SQLite。
- 明文 manifest。
- 缓存文件。
- 密钥文件。
- 未脱敏日志。

允许持久化：

- Device Token 凭据文件，但必须在用户机器 DPAPI 或显式测试明文模式下。
- password KDF salt 和 Argon2id 参数，但不得包含授权密码或 wrapping key。
- 非敏感远端 `.meta.json` 和 `job.index.json`。

## 缓存与长路径

缓存目录结构必须按 job 隔离。明文 manifest、staging、verify、tmp 阶段结束后要删除。archive 可按缓存等级保留或清理。

Windows 长路径约束：

- archive、payload staging、manifest staging、verify、restore、上传读取都可能触发长路径问题。
- 产品代码读取、移动、删除深层缓存路径时必须使用长路径安全包装。
- 测试临时目录优先放在仓库内短路径 `.cache/pt` 或 `.cache/pytest-*`。

## 构建与部署

客户端：

```powershell
.\client_build.ps1 -DryRun
.\client_build.ps1
```

如果执行策略阻止脚本：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\client_build.ps1 -DryRun
```

云端：

```powershell
.\go_build.ps1
```

服务端正常部署只需启动 `cloud-api serve`。`cloud-api migrate` 只作为排障或人工修复入口，不作为正常部署前置条件。

## 测试命令

文档-only 变更：

```powershell
git diff --check
```

客户端测试：

```powershell
cd client
$env:UV_LINK_MODE='copy'
$env:UV_CACHE_DIR='..\.cache\uv'
$env:TMP='..\.cache\tmp'
$env:TEMP='..\.cache\tmp'
uv sync
uv run python -m pytest -p no:cacheprovider --basetemp ..\.cache\pytest-basetemp
uv run python -m compileall src tests
```

Go 测试：

```powershell
cd cloud-api
$env:GOCACHE='..\.cache\go-build'
$env:GOMODCACHE='..\.cache\go-mod'
go test -p=1 ./...
```

真实 Cloud Sync 审计：

```powershell
cd client
$env:UV_LINK_MODE='copy'
$env:UV_CACHE_DIR='..\.cache\uv'
$env:CLOUD_API_BASE_URL='https://backup.baichengedu.com'
uv run python -m auto_backup_client.cloud_sync_audit_cli
```

## 技术审计结论

旧文档的主要问题不是缺少信息，而是信息归属混乱。技术约束散落在 PRD、README、进度记录、手动验收和沉淀问题中，导致开发者容易只读到其中一份就误判边界。

新口径中：

- 产品能力归 `prd.md`。
- 技术实现归 `tech.md`。
- 任务拆解归 `spec.md`。
- 协作和测试执行归 `agents.md`。
- 冲突和修正归 `audit.md` 与 `document_change_audit.md`。
