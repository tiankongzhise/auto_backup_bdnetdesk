# 开发排期与进度

本文件用于记录项目开发排期、当前进度和提交后的完成情况。每次开发开始时和 commit 后都必须更新本文件。

## 当前阶段

Go 云端同步服务与 outbox 同步边界已完成。

## 当前工作项

- 完成云端服务技术栈从 FastAPI 到 Go + PostgreSQL 的调整。
- 完成客户端 `sync_outbox` 与云端 Cloud Sync API 的职责边界补充。
- 完成 Go Cloud Sync API 单二进制服务骨架。
- 完成 PostgreSQL 云端同步 schema、revision 幂等写入和查询接口。
- 完成设备 token 认证、同步接口和基础测试。

## 本次验收标准

- 产品规格文档明确 Go 替代 FastAPI 作为 v1.3 唯一云端 API。
- 开发排期包含客户端 `sync_outbox` 和 Go Cloud Sync API 阶段。
- Go 服务提供设备注册、设备 token 认证、批量 revision 同步、内容/归档查询、校对摘要和健康检查接口。
- PostgreSQL 迁移包含 `devices`、`cloud_entities`、`entity_revisions`、`content_objects`、`archive_objects`。
- Go 单元测试覆盖认证、幂等重复提交、版本冲突和基础查询。
- `go test ./...` 通过。

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
- 新增 `cmd/cloud-api` 单二进制入口，使用 chi 路由、pgx/pgxpool 连接 PostgreSQL。
- 新增设备注册接口，云端只保存 Device Token 哈希，后续接口通过 Bearer Token 认证。
- 新增批量 revision 同步接口，支持 `entity_id + revision_id` 幂等、`data_version` 冲突检测和逐条同步结果返回。
- 新增内容对象、归档对象和校对摘要查询接口。
- 新增 PostgreSQL 迁移 `migrations/postgres/001_cloud_sync.sql`，包含 `devices`、`cloud_entities`、`entity_revisions`、`content_objects`、`archive_objects`。
- 新增 SQLite 迁移 `migrations/sqlite/001_sync_outbox.sql`，明确客户端本地 `sync_outbox` 表契约。
- 新增 `sqlc.yaml` 和 SQL 查询文件作为后续 sqlc 生成入口；当前环境缺少 sqlc，保留可编译的 pgx 手写数据层。
- 新增 Go 单元测试，覆盖设备认证、重复 revision 幂等、版本冲突、内容查询、归档查询和非法 payload 拒绝。

提交摘要：Go 云端 API 已具备 v1.3 outbox 同步接收、幂等写入、冲突检测和基础查询能力；客户端仍禁止直连 PostgreSQL。

后续待办：

- 搭建 Python 客户端项目骨架、配置加载和日志脱敏。
- 在客户端 SQLite 仓储层实现业务写入与 `sync_outbox` 同事务提交。
- 安装并验证 sqlc 后，可在单独提交中将 Go 数据层切换为生成代码。
