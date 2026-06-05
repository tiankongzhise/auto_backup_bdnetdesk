# auto_backup_bdnetdesk

Windows 桌面端百度网盘加密备份工具。

本项目目标是将用户选择的文件和文件夹，以加密压缩包形式备份到百度网盘指定位置，并通过本地 SQLite 与 Go 云端 API + PostgreSQL 双写记录，实现多设备去重、断点续传、来源映射、数据库与百度网盘校对、原始数据清理记录和恢复。

## 当前状态

项目已完成产品规格和初始化文档，尚未开始代码实现。

## 文档入口

- `AGENTS.MD`：项目记忆、协作规则、编码约束、提交规则和安全约束。
- `docs/product_spec_v1.3.md`：完整产品与技术规格。
- `docs/roadmap_progress.md`：开发排期和进度记录。
- `.env.example`：本地开发环境变量示例。

## 云端同步服务

Go 云端服务入口为 `cmd/cloud-api`，默认监听 `:8080`，通过 `POSTGRES_DSN` 或 PostgreSQL 分项环境变量连接外部 PostgreSQL。

```powershell
go run ./cmd/cloud-api
```

数据库迁移文件位于 `migrations/postgres`；客户端本地 `sync_outbox` 迁移契约位于 `migrations/sqlite`。

## 敏感信息

不要提交 `.env`、真实数据库连接串、百度 token、用户密码、本地 SQLite 数据库、缓存文件、日志或密钥文件。

`.env.example` 只提供占位示例。百度 access token、refresh token 和用户备份密码应由客户端使用 Windows DPAPI 或 Windows 凭据管理器保存。
