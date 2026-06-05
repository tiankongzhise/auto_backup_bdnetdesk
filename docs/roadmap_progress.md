# 开发排期与进度

本文件用于记录项目开发排期、当前进度和提交完成情况。每次开发开始前必须更新当前工作项、计划修改范围和验收标准；每个小功能完成后，必须先更新完成记录、提交摘要和后续待办，再将本文件与代码和文档一起纳入同一个 commit。

## 当前阶段

Go 云端服务构建脚本适配已完成。

## 当前工作项

- 完成根目录通用 `go_build.ps1` 对本项目 `cloud-api/` Go module 的适配。
- 完成默认生成服务端部署用 `linux/amd64` Go 二进制文件。
- 完成构建产物输出到项目级部署产物目录，避免在源码目录或仓库根目录散落二进制。
- 完成构建脚本文档、忽略规则和本次工具问题沉淀。

## 本次验收标准

- 在仓库根目录执行 `.\go_build.ps1` 可成功构建 `cloud-api/cmd/cloud-api`。
- 默认输出路径为 `dist/cloud-api/linux-amd64/cloud-api`，二进制名称与 systemd 示例保持一致。
- 脚本支持显式指定 `-ModuleDir`、`-ServiceName`、`-OutputDir`、`-GoOS`、`-GoArch` 和 `-OutputName`。
- Go 缓存和构建产物不纳入 Git 提交，不污染 `cloud-api/` 源码目录。
- 在 `cloud-api/` 下执行 `go test ./...` 通过。

## 开发排期

| 阶段 | 工作内容 | 状态 |
| --- | --- | --- |
| 0 | 仓库初始化、项目记忆、产品文档、进度文件 | 已完成 |
| 1 | Python 项目骨架、配置加载、日志脱敏、基础目录结构 | 未开始 |
| 2 | SQLite schema、版本字段、客户端 `sync_outbox`、迁移机制 | 未开始 |
| 3 | Go Cloud Sync API、PostgreSQL schema、revision 幂等写入、设备认证 | 已完成 |
| 4 | PySide6 基础 UI、任务页、设置页 | 未开始 |
| 5 | 扫描、快速指纹、完整 MD5/SHA256、文件夹哈希 | 未开始 |
| 6 | 去重索引、本地/云端内容对象、来源引用 | 未开始 |
| 7 | 7-Zip 加密压缩、manifest、标准/严格验证 | 未开始 |
| 8 | 百度 OAuth、预上传、分片上传、创建文件 | 未开始 |
| 9 | 断点续传、上传恢复、失败重试 | 未开始 |
| 10 | 缓存额度、动态清理等级、缓存 artifact 管理 | 未开始 |
| 11 | 来源与远端映射、数据库/百度校对 UI | 未开始 |
| 12 | 原始数据清理记录、恢复到原路径/手动路径 | 未开始 |
| 13 | 打包、验收测试、使用文档 | 未开始 |

## 完成记录

### 初始化项目仓库与产品文档

- 完成 Git 仓库初始化。
- 新增 `AGENTS.MD`，记录项目记忆、PowerShell UTF-8 约束、提交规则、敏感信息规则和架构约束。
- 新增 `docs/product_spec_v1.3.md`，落地完整产品与技术规格。
- 新增 `.env.example`，提供本地数据库、云端 API、PostgreSQL 和百度开放平台配置占位。
- 新增 `.gitignore`，排除 `.env`、本地数据库、日志、缓存和密钥目录。
- 新增 `README.md`、`.editorconfig` 和 `.gitattributes`。

### 修正 Git 换行策略

- 发现初始化提交时 Git 提示部分文本文件后续可能被 CRLF 替换。
- 已将仓库本地 `core.autocrlf` 设置为 `false`。
- 已在 `.gitattributes` 中使用 `* text=auto eol=lf`，并对常见文本文件类型明确指定 LF。
- 已将该问题和后续约束补充到 `AGENTS.MD`。

下一步计划：搭建 Python 项目骨架、配置加载、日志脱敏和基础目录结构。

### Go 云端同步服务与 outbox 同步边界

- 将产品规格中的云端服务从 FastAPI + PostgreSQL 调整为 Go + PostgreSQL。
- 在产品规格第 5 节补充客户端 `sync_outbox`、Go Cloud Sync API、Device Token、PostgreSQL 表、幂等写入、冲突检测和查询接口的详细契约。
- 新增 `cloud-api/cmd/cloud-api` 单二进制入口，使用 chi 路由、pgx/pgxpool 连接 PostgreSQL。
- 新增设备注册接口，云端只保存 Device Token 哈希，后续接口通过 Bearer Token 认证。
- 新增批量 revision 同步接口，支持 `entity_id + revision_id` 幂等、`data_version` 冲突检测和逐条同步结果返回。
- 新增内容对象、归档对象和校对摘要查询接口。
- 新增 PostgreSQL 迁移 `cloud-api/migrations/postgres/001_cloud_sync.sql`，包含 `devices`、`cloud_entities`、`entity_revisions`、`content_objects`、`archive_objects`。
- 新增 SQLite 迁移 `client/migrations/sqlite/001_sync_outbox.sql`，明确客户端本地 `sync_outbox` 表契约。
- 新增 `cloud-api/sqlc.yaml` 和 SQL 查询文件作为后续 sqlc 生成入口；当前环境缺少 sqlc，保留可编译的 pgx 手写数据层。
- 新增 Go 单元测试，覆盖设备认证、重复 revision 幂等、版本冲突、内容查询、归档查询和非法 payload 拒绝。

提交摘要：Go 云端 API 已具备 v1.3 outbox 同步接收、幂等写入、冲突检测和基础查询能力；客户端仍禁止直连 PostgreSQL。

后续待办：

- 搭建 Python 客户端项目骨架、配置加载和日志脱敏。
- 在客户端 SQLite 仓储层实现业务写入与 `sync_outbox` 同事务提交。
- 安装并验证 sqlc 后，可在单独提交中将 Go 数据层切换为生成代码。

### Go 服务目录整理与数据库一致性规则修正

- 将 Go 云端服务的 `go.mod`、`go.sum`、`sqlc.yaml`、`cmd/`、`internal/` 和 PostgreSQL 迁移统一移动到 `cloud-api/`。
- 将客户端本地 SQLite `sync_outbox` 迁移移动到 `client/migrations/sqlite/001_sync_outbox.sql`，明确其不属于 Go 服务端迁移。
- 将 Go module 调整为 `auto_backup_bdnetdesk/cloud-api`，并修正服务入口 import。
- 新增 `cloud-api/.env.example`，说明 Go 服务端通过 `POSTGRES_DSN` 或 PostgreSQL 分项环境变量连接目标 PostgreSQL。
- 更新 README 和产品规格，补充二进制部署到服务器后的 systemd `EnvironmentFile=/etc/auto-backup-bdnetdesk/cloud-api.env` 配置方式。
- 更新产品规格的一致性口径：当前云端数据库是 `cloud_entities + entity_revisions` 的 revision 投影与审计层，不等同于所有本地业务表已建立同名云端物理表；校对以 `entity_id`、`revision_id`、`data_version`、`canonical_record_sha256` 为准。
- 修正 `AGENTS.MD` 和本文件的进度规则，要求功能完成记录在提交前写入，并与代码和文档进入同一个 commit。
- 发现 `go build ./cmd/cloud-api` 在 Windows 下会生成 `cloud-api.exe`，已删除本地构建产物并在 `.gitignore` 中忽略 `*.exe`，同时将该约束沉淀到 `AGENTS.MD`。

提交摘要：Go 云端服务目录已收拢，部署配置来源和数据库一致性边界已明确，进度文件提交规则已改为提交前更新。

后续待办：

- SQLite schema 阶段补齐本地核心业务表，并决定云端继续使用 JSONB 投影、增加同名物理表，或增加视图/索引表。
- 后续构建检查优先使用 `go build -o` 输出到临时目录，避免生成本地 `.exe` 污染工作区。
- 继续搭建 Python 客户端项目骨架、配置加载和日志脱敏。

### Go 服务端部署构建脚本适配

- 新增根目录 `go_build.ps1`，默认从仓库根目录自动定位 `cloud-api/` Go module，并自动识别唯一的 `cmd/cloud-api/main.go` 服务入口。
- 默认构建目标为 `linux/amd64`，启用 `CGO_ENABLED=0`、`-trimpath`、`-buildvcs=false` 和 `-ldflags="-s -w"`，生成适合服务器部署的精简单二进制。
- 默认输出路径为 `dist/cloud-api/linux-amd64/cloud-api`，二进制名称与 README 和产品规格中的 systemd `ExecStart=/opt/auto-backup-bdnetdesk/cloud-api` 保持一致。
- 支持 `-ModuleDir`、`-ServiceName`、`-OutputDir`、`-OutputName`、`-GoOS`、`-GoArch`、`-NameSource` 和 `-CompressWithUpx` 参数，保留通用构建脚本能力。
- 将 Go 构建缓存统一放入仓库根目录 `.cache/go-build` 和 `.cache/go-mod`，并在 `.gitignore` 中忽略 `.cache/` 与 `dist/`。
- 在 `.gitattributes` 中补充 `*.ps1 text eol=lf`，保持 PowerShell 脚本符合项目 LF 约束。
- 更新 README，补充从仓库根目录执行 `.\go_build.ps1` 生成服务端部署二进制的说明。
- 将本轮遇到的 Windows PowerShell 5.1 UTF-8 文件读取和 .NET API 兼容性问题沉淀到 `AGENTS.MD`。
- 已验证在仓库根目录执行 `.\go_build.ps1` 成功生成 `dist/cloud-api/linux-amd64/cloud-api`。
- 已验证在 `cloud-api/` 下执行 `go test ./...` 通过。

提交摘要：根目录通用 Go 构建脚本已适配本项目 `cloud-api/` 服务端模块，可直接生成 Linux amd64 部署二进制，构建缓存和产物已隔离并忽略。

后续待办：

- 后续发布阶段可增加二进制 SHA256 校验文件、版本号注入和部署包打包流程。
- 继续搭建 Python 客户端项目骨架、配置加载和日志脱敏。
