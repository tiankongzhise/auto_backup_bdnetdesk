# 项目记忆与协作规则

本文件是本项目的 AI 通用记忆文件。任何 AI 助手、自动化代理或开发者在修改本项目之前，都必须先阅读本文件。

## 开发前必读

每次开始开发前必须阅读：

- `AGENTS.MD`
- `docs/roadmap_progress.md`
- 当前相关产品文档或技术文档

阅读后再开始修改代码、文档或配置。

## 沙箱问题沉淀

本项目在沙箱或本地工具运行过程中，如果遇到编码、路径、权限、命令行为、依赖、测试、构建、打包等问题，必须在解决后把问题和约束方案补充到本文件。

记录格式：

```text
日期：
问题：
原因：
解决方案：
后续约束：
```

这样做的目的是避免后续重复踩同一个问题。

## PowerShell UTF-8 约束

本项目在 PowerShell 中执行命令时必须使用 UTF-8，避免中文读取、写入或命令输出出现乱码。

每次 PowerShell 会话开始时执行：

```powershell
$OutputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
chcp 65001
```

项目文本文件统一使用 UTF-8 编码。

## 提交规则

- 每完成一个小功能必须进行一次 Git commit。
- commit message 必须使用中文。
- commit message 必须详述本次开发的技术细节和项目变化。
- 不允许把多个无关功能合并到一个模糊提交中。
- 每次开发开始时和 commit 后都必须更新 `docs/roadmap_progress.md`。

## 进度记录

本项目的开发排期和进度必须记录在 `docs/roadmap_progress.md`。

每次开发开始时必须更新：

- 当前工作项
- 计划修改范围
- 验收标准

每次 commit 后必须更新：

- 完成记录
- 提交摘要
- 后续待办

## 敏感信息规则

不得提交以下内容：

- `.env`
- 真实数据库连接串
- 百度 access token
- 百度 refresh token
- 用户备份密码
- 本地 SQLite 数据库
- 明文 manifest
- 本地缓存文件
- 日志中的敏感信息
- 密钥文件

`.env.example` 只能包含占位示例，不得包含真实密钥。

百度 access token、refresh token、用户备份密码不应明文保存到 `.env`。客户端运行时应使用 Windows DPAPI 或 Windows 凭据管理器保存敏感凭据。

## 架构约束

- 本地 SQLite 是任务执行主库。
- 只要本地数据库落盘成功，任务即可继续执行。
- 客户端本地 `sync_outbox` 通过云端 API 异步同步到 PostgreSQL，保证最终一致。
- 客户端不得直连云端数据库，必须通过云端 API 访问。
- 本地数据库与云端数据库需要双写并带版本控制。
- 本地/云端版本冲突不得自动覆盖，必须进入校对流程。

## 百度网盘远端目录约束

百度远端目录必须按日期、设备、任务组织：

```text
/apps/{appname}/backups/{yyyy}/{MM}/{dd}/{device_id}/{job_id}/
```

不得使用 `archive_sha256` 前缀作为远端目录分桶。

压缩包文件名可以包含 `archive_sha256`，用于唯一性和校验：

```text
{archive_seq}-{archive_sha256}.7z
{archive_seq}-{archive_sha256}.meta.json
```

## 产品安全约束

- 内容指纹不得混入文件名、路径、时间、设备等附属信息。
- 最终去重只能使用完整 SHA256 和文件大小。
- 明文 manifest 只允许短暂存在于缓存临时目录，压缩包验证通过后必须删除。
- 原始数据清理只能由用户手动触发，默认移入 Windows 回收站。
- 恢复覆盖默认关闭，默认保留两者。

## 已沉淀问题

### 2026-06-05：Git 自动换行与项目 LF 约束冲突

问题：初始化提交时 Git 提示 `.editorconfig`、`.env.example`、`.gitattributes`、`.gitignore` 的 LF 会在后续被替换为 CRLF。

原因：当前仓库继承了 `core.autocrlf=true`，与项目文档要求的 UTF-8 + LF 文本文件约束冲突。

解决方案：本仓库设置 `core.autocrlf=false`，并在 `.gitattributes` 中使用 `* text=auto eol=lf`，同时对 `.md`、`.txt`、`.env`、`.py`、`.json`、`.yml`、`.yaml` 明确指定 LF。

后续约束：本项目不得依赖全局 Git 换行配置；发现换行警告时必须优先检查仓库本地 `core.autocrlf` 和 `.gitattributes`。

### 2026-06-05：本地缺少 sqlc 命令

问题：落地 Go 云端同步服务时，检查本地工具链发现 `sqlc` 命令不存在。

原因：当前开发环境只有 Go 工具链，尚未安装 sqlc 代码生成器。

解决方案：本次提交保留 `sqlc.yaml` 和 `internal/cloudapi/queries` 查询文件作为生成入口，同时先用 `pgx` 手写窄数据层，保证项目可以直接 `go test ./...`。

后续约束：需要启用 sqlc 生成代码前，先安装 sqlc 并在单独提交中切换数据层；不得在未验证生成结果时删除当前可编译的 pgx 数据层。
