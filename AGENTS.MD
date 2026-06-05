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
- 每次开发开始前必须更新 `docs/roadmap_progress.md` 的当前工作项、计划范围和验收标准。
- 每个小功能完成后，必须先更新 `docs/roadmap_progress.md` 的完成记录、提交摘要和后续待办，再将代码、文档和进度文件一起纳入同一个 commit。

## 进度记录

本项目的开发排期和进度必须记录在 `docs/roadmap_progress.md`。

每次开发开始时必须更新：

- 当前工作项
- 计划修改范围
- 验收标准

每个小功能提交前必须更新：

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

解决方案：本次提交保留 `cloud-api/sqlc.yaml` 和 `cloud-api/internal/cloudapi/queries` 查询文件作为生成入口，同时先用 `pgx` 手写窄数据层，保证项目可以在 `cloud-api/` 下直接 `go test ./...`。

后续约束：需要启用 sqlc 生成代码前，先安装 sqlc 并在单独提交中切换数据层；不得在未验证生成结果时删除当前可编译的 pgx 数据层。

### 2026-06-05：Go build 在服务目录生成 Windows 二进制

问题：在 `cloud-api/` 下执行 `go build ./cmd/cloud-api` 后生成 `cloud-api.exe`，导致工作区出现未跟踪构建产物。

原因：Windows 下未指定 `-o` 输出路径时，Go 会在当前模块目录生成可执行文件。

解决方案：删除本地生成的 `cloud-api.exe`，并在 `.gitignore` 中忽略 `*.exe`。

后续约束：后续仅做编译检查时，优先使用 `go build -o` 输出到临时目录；不得提交本地构建出的 `.exe` 二进制。

### 2026-06-05：Windows PowerShell 5.1 读取 UTF-8 无 BOM 中文文件乱码

问题：在 PowerShell 中已设置 UTF-8 控制台编码后，使用 `Get-Content -Raw` 读取中文 Markdown 仍出现乱码。

原因：当前环境是 Windows PowerShell 5.1，读取 UTF-8 无 BOM 文本时未显式指定 `-Encoding UTF8` 可能按系统默认 ANSI 编码解码；控制台输出编码不等同于文件读取编码。

解决方案：读取项目中文文本文件时使用 `Get-Content -Raw -Encoding UTF8 -LiteralPath ...`；脚本内部读取 `go.mod` 等文本也显式使用 `-Encoding UTF8`。

后续约束：PowerShell 命令除设置 `$OutputEncoding`、`[Console]::OutputEncoding`、`[Console]::InputEncoding` 和 `chcp 65001` 外，读取中文或项目文本文件时必须显式指定 `-Encoding UTF8`。

### 2026-06-05：Windows PowerShell 5.1 不支持 `System.IO.Path.GetRelativePath`

问题：改造根目录 `go_build.ps1` 时，原计划使用 `[System.IO.Path]::GetRelativePath(...)` 输出相对路径。

原因：当前环境是 Windows PowerShell 5.1 / .NET Framework，该 API 属于较新的 .NET 版本，不适合作为本项目通用脚本依赖。

解决方案：`go_build.ps1` 改用 `System.Uri.MakeRelativeUri(...)` 计算相对路径，保持 Windows PowerShell 5.1 兼容。

后续约束：项目通用 PowerShell 脚本必须优先兼容 Windows PowerShell 5.1；使用 .NET API 前应确认该 API 在 5.1 环境可用。

### 2026-06-05：Git 提交后自动维护传入不兼容 `--detach` 参数

问题：提交 LGPL-2.1 发布信息后，提交本身成功，但随后 Git 输出 `git maintenance run` 的 `unknown option 'detach'` 报错。

原因：当前 Git for Windows 为 `2.54.0.windows.1`，本仓库没有显式 maintenance/gc/scalar 配置，hooks 也只有 sample；单独执行 `git maintenance run --auto` 正常，报错判断来自提交后的自动维护流程尝试传入当前 `git maintenance run` 子命令不支持的 `--detach` 参数。

解决方案：在本仓库设置 `maintenance.auto=false`，避免提交后自动触发该不兼容维护流程；需要维护时可手动执行 `git maintenance run --auto`。

后续约束：后续若提交后再次出现 maintenance 参数兼容性报错，优先检查 `git config --show-origin --get-regexp '^(maintenance|gc|scalar)\.'` 和 Git for Windows 版本；不要把该报错误判为提交失败，应先用 `git log --oneline -1` 和 `git status --short --branch` 确认提交状态。
