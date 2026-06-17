# 项目记忆与协作规则

本文件是本项目的 AI 通用记忆文件。任何 AI 助手、自动化代理或开发者在修改本项目之前，都必须先阅读本文件。

## 开发前必读

每次开始开发前必须阅读：

- `AGENTS.MD`
- `docs/current/README.md`
- `docs/roadmap_progress.md`
- 当前相关权威文档：`docs/current/prd.md`、`docs/current/design.md`、`docs/current/tech.md`、`docs/current/api_database_contract.md`、`docs/current/spec.md`、`docs/current/agents.md`
- Git 最近提交历史和关键提交摘要

阅读后再开始修改代码、文档或配置。

## 当前文档权威顺序

本项目已将开发文档细化到 `docs/current/`。后续开发不得只依赖旧的长规格文档或进度流水判断边界。

权威顺序：

1. `docs/current/README.md`：当前权威文档入口、阅读路径和旧文档状态。
2. `docs/current/prd.md`：产品需求、用户流程、支持/不支持范围和产品验收。
3. `docs/current/spec.md`：全 v1.3 任务拆解、状态、依赖、边界和验收标准。
4. `docs/current/design.md`：pywebview 前端设计、页面规范、交互与展示边界。
5. `docs/current/tech.md`：技术栈、架构、凭据、缓存、构建和测试约束。
6. `docs/current/api_database_contract.md`：云端 HTTP API、pywebview bridge API、百度 API、SQLite/PostgreSQL schema、同步 payload 和版本字段契约。
7. `docs/current/agents.md`：前后端开发规范、测试命令、提交和文档维护规则。
8. `docs/current/audit.md`：旧文档冲突、模糊边界、风险和待验证问题。
9. `docs/current/document_change_audit.md`：新旧文档口径修正记录，用于后续与代码和测试变更校对。

旧文档保留用于追溯，状态见 `docs/legacy/README.md`。当旧文档与 `docs/current/` 冲突时，以 `docs/current/` 为准，并在 `document_change_audit.md` 中记录修正原因。

## Git 历史阅读约束

每次进入新阶段或开始修改前，必须阅读 Git 历史，了解项目实际演进过程、已完成能力、已踩坑和提交边界。

建议至少执行并阅读：

```powershell
git log --oneline --decorate -n 20
git log --date=short --pretty=format:'%h%x09%ad%x09%s' -n 20
```

如当前工作项依赖近期能力，还必须阅读相关关键提交：

```powershell
git show --stat --summary --format=fuller <commit>
```

后续约束：不得只依据当前文件快照推断项目进度；涉及架构、授权、凭据、上传、迁移、部署或测试链路时，必须结合 Git 历史和 `docs/roadmap_progress.md` 判断上下文。

## 官方 API 文档获取约束

涉及百度网盘、OpenAI、云端服务、依赖库或其他外部 API/SDK 的接口开发时，必须优先获取官方文档并记录实现依据。

获取顺序：

1. 先使用可用的浏览/搜索工具打开官方文档 URL。
2. 如果浏览工具无法访问，使用 `curl.exe -L` 获取官方文档内容。
3. 如果 `curl.exe` 仍失败，使用 Python 脚本基于 `urllib` 或 `httpx` 获取。
4. 如果以上方式都失败，必须在 `docs/roadmap_progress.md` 或相关开发说明中警示“未获取到官方文档内容”，并明确本次实现依据来自项目既有规格、历史提交、已验证接口行为或其他来源。

后续约束：不得在无法获取官方文档时假装已经核验；涉及外部 API 参数、错误码、鉴权、请求体或响应体时，必须保留可追溯依据，方便后续校对和回滚。

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

## Python 客户端依赖管理约束

- Python 客户端代码、测试、依赖声明和客户端专用文档必须集中放在 `client/` 目录。
- Python 客户端虚拟环境和依赖必须使用 uv 管理。
- 新增依赖使用 `uv add`，移除依赖使用 `uv remove`，同步环境使用 `uv sync`。
- 同步依赖时直接在 `client/` 下执行 `uv sync`，不需要先执行 `uv venv`。
- 不得使用 `pip install` 直接安装或变更 Python 客户端依赖。
- `uv.lock` 作为客户端依赖锁文件需要随依赖变更提交。
- `.venv/`、构建缓存和下载缓存不得提交。

## 真实云端 API 联调约束

- 当前 `https://backup.baichengedu.com` 真实云端 API 已部署可用。
- 后续客户端与百度授权、账号选择、token 回写、上传、同步等联调必须使用真实云端 API 链路，不得以模拟云端 API 作为联调验收依据。
- 自动化单元测试可以覆盖纯本地输入校验、路径构造、分片计算、UI 状态和无网络逻辑；但凡验证客户端与云端接口契约、授权链路、账号状态、百度上传/删除 API 行为或同步结果，必须走真实云端 API 或真实百度网盘 API，不得使用 mock API 作为验收依据。
- 百度上传链路测试必须生成临时测试文件真实上传，覆盖小文件、跨分片文件、冲突等关键场景；测试完成后使用百度官方文件管理删除接口清理本批远端测试文件。测试数据库脏数据上线前可整体删除，不作为本阶段阻塞。
- 真实联调产生的 Device Token、百度授权 token、用户密码、wrapping key、本地数据库和日志敏感内容不得提交到仓库。

## 进度记录

本项目的开发排期和进度必须记录在 `docs/roadmap_progress.md`。

后续开发必须遵循 `docs/roadmap_progress.md` 中的开发排期推进。每次开始新开发前，必须先确认当前工作项属于排期中的哪个优先级和阶段；不得跳过高优先级未完成阶段去做低优先级功能，除非属于安全修复、数据一致性修复、真实联调阻塞修复或用户明确要求的新增需求。

如需插入临时 fix 或新增需求，必须在开始修改前同步更新 `docs/roadmap_progress.md`：

- 在当前工作项中写明临时工作内容。
- 在验收标准中写明本次临时工作的可验证结果。
- 在排期变更记录中写明原因、影响阶段、是否打乱既定顺序、完成后如何回到主排期。
- 如影响已有阶段状态，必须更新开发排期表中的状态和下一步验收。

每次开发开始时必须更新：

- 当前工作项
- 计划修改范围
- 验收标准

每个小功能提交前必须更新：

- 完成记录
- 提交摘要
- 后续待办

每个小功能完成后，必须把实际完成范围与排期阶段重新对齐；如果实现范围小于原计划，必须明确剩余差距；如果实现范围超出原计划，必须说明新增能力、影响阶段和后续验证要求。

## 开发阶段接力规则

每次开发开始时，必须在 `docs/roadmap_progress.md` 的“当前工作项”中明确写明：

```text
本次开发阶段：P? 阶段 N <阶段名>
```

每次开发完成后，必须在 `docs/roadmap_progress.md` 的“完成记录”和“开发排期”中明确写明：

```text
P? 阶段 N <阶段名> 开发完成；下一个开发阶段为 P? 阶段 N+1 <阶段名>
```

如本次属于临时 fix、真实联调阻塞修复、安全修复或用户插入需求，仍必须写明其挂靠阶段、是否改变主排期、完成后回到哪个阶段。不得只写“已完成”或“下一步继续开发”这类无法让后续人员快速定位进度的描述。

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
- Go 云端二进制部署后不得依赖人工先执行初始化命令；服务启动必须自检 PostgreSQL schema，缺少关键表/列或检查失败时自动执行内置迁移并复查。
- `cloud-api migrate --env-file /path/to/.env` 只允许作为排障或人工修复入口，不得作为正常部署的初始化前置条件。

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

### 2026-06-05：宝塔面板环境变量未进入 Go 服务进程

问题：Go 云端服务部署到宝塔面板后，日志显示连接 PostgreSQL 时使用默认 `auto_backup_user/auto_backup_bdnetdesk`，实际 `.env` 中配置的数据库用户和库名未生效。

原因：服务原先只通过 `os.Getenv` 读取进程环境变量，不会主动读取 `.env` 文件；宝塔面板环境变量配置未可靠注入到服务进程时，代码会回退到默认 PostgreSQL 分项配置，导致连接到错误用户和库名。

解决方案：Go 服务新增 `--env-file /path/to/.env` 和 `CLOUD_API_ENV_FILE` 显式环境文件加载能力，未显式指定时自动尝试工作目录、二进制目录下的 `cloud-api.env`/`.env`，Linux 下额外尝试 `/etc/auto-backup-bdnetdesk/cloud-api.env`；`.env` 只填充缺失变量，不覆盖已注入的非空进程环境变量。启动和 PostgreSQL 连接失败日志新增脱敏配置摘要。

后续约束：宝塔部署优先在启动命令中显式添加 `--env-file /实际路径/.env`，不要只依赖面板环境变量注入；排查数据库连接问题时优先查看 `env_files_loaded`、`postgres_config_source`、`postgres_user`、`postgres_database` 和 `postgres_defaulted_fields`，不得在日志中输出完整 DSN、数据库密码、百度 App Secret 或 token。

### 2026-06-05：uv 在当前工作区硬链接依赖失败后回退复制

问题：在 `client/` 下执行 `uv add` 安装 Python 客户端依赖时，uv 提示 hardlink 失败并回退到 full copy。

原因：uv 缓存目录和当前工作区可能位于不同文件系统或当前环境不支持硬链接，导致依赖文件无法硬链接到 `.venv`。

解决方案：后续执行 uv 命令时设置 `UV_LINK_MODE=copy`，显式使用复制模式，避免重复出现 hardlink fallback 警告。

后续约束：Python 客户端依赖仍必须使用 uv 管理；在 `client/` 下执行 `uv add`、`uv remove`、`uv sync` 或 `uv run` 时优先设置 `UV_LINK_MODE=copy`，不得改用 `pip install` 绕过 uv。

### 2026-06-05：PySide6 从默认源下载过慢导致 uv 超时

问题：新增 PySide6 和 qrcode 依赖后，`uv add`/`uv sync` 从默认源下载 PySide6 大体积 wheel 时多次超过 2-5 分钟超时，并留下后台 `uv` 进程。

原因：PySide6 依赖包含 `pyside6-addons`、`pyside6-essentials` 等大文件，当前网络访问默认 PyPI 源速度不稳定。

解决方案：在 `client/pyproject.toml` 配置 `tool.uv.index-url = "https://pypi.tuna.tsinghua.edu.cn/simple"`，继续配合 `UV_LINK_MODE=copy` 执行 `uv sync`，依赖同步在 82 秒内完成。

后续约束：后续客户端依赖安装和同步优先使用 `client/pyproject.toml` 中配置的 PyPI 镜像；若 uv 命令超时，先检查并结束遗留 `uv` 进程，再重试，不得改用 `pip install` 绕过 uv。

### 2026-06-05：真实云端 PostgreSQL 未执行迁移导致设备注册失败

问题：真实云端 `https://backup.baichengedu.com/v1/healthz` 和 `/v1/readyz` 返回 200，但调用 `/v1/devices/register` 返回 500，服务器日志显示 `ERROR: relation "devices" does not exist (SQLSTATE 42P01)`。

原因：服务进程能连接 PostgreSQL，所以原 `/readyz` 只做 Ping 会误报可用；目标数据库未执行 `001_cloud_sync.sql`，缺少 `devices` 等关键表。

解决方案：补充 Go 单二进制内置迁移和 schema readiness 检查；`cloud-api serve` 启动时自动检查 PostgreSQL schema，缺少关键表/列或检查失败时自动执行内置迁移并复查；`/v1/readyz` 后续必须同时检查 PostgreSQL 可连接和关键表存在。

后续约束：真实云端联调前必须确认新版服务已重启且 `/v1/readyz` 返回 200；遇到 `relation ... does not exist` 或 `schema_not_ready` 时优先检查启动日志中的自动迁移结果和 PostgreSQL 数据库是否连错，`cloud-api migrate` 只作为排障手动入口，不得仅凭 `/readyz` 旧版本 200 判断数据库 schema 可用。

### 2026-06-05：uv 默认缓存和受管 Python 目录受沙箱权限影响

问题：在 `client/` 下执行 `uv sync` 时，uv 默认访问 `C:\Users\3700x\AppData\Local\uv\cache` 失败；执行 `uv run` 时读取 `C:\Users\3700x\AppData\Roaming\uv\python` 受沙箱权限限制失败。

原因：当前工作区写权限只覆盖仓库和临时目录，uv 默认缓存和受管 Python 元数据位于用户目录；同时本机 uv 缓存目录状态会触发 “当文件已存在时，无法创建该文件”。

解决方案：后续 uv 命令继续设置 `UV_LINK_MODE=copy`，并显式设置 `UV_CACHE_DIR` 到仓库内 `.cache/uv`；若 uv 仍需读取用户目录下的受管 Python 信息，则按权限流程申请提升后运行，不改用 `pip install`。

后续约束：客户端依赖同步和测试命令优先使用 `UV_LINK_MODE=copy`、`UV_CACHE_DIR=<repo>/.cache/uv`；`.cache/`、`.venv/` 和 pytest/egg 缓存不得提交。

### 2026-06-05：uv venv Python 启动器在沙箱内无法创建进程

问题：在 `client/` 下已设置 `UV_LINK_MODE=copy` 和仓库内 `UV_CACHE_DIR` 后，`uv run pytest`、`uv run python --version` 和 `uv run python -m pytest` 均失败，提示无法使用 `client\.venv\Scripts\python.exe` 创建进程，错误尾部显示乱码问号。

原因：`uv sync` 可在沙箱内完成，但当前沙箱对 `.venv\Scripts\python.exe` 子进程启动仍有限制；同一 `uv run` 命令提升权限后可正常启动并运行测试。

解决方案：本轮按权限流程提升后执行 `uv run python -m pytest` 和 `uv run python -m compileall src tests`，均通过；继续使用 uv 管理依赖，不改用 `pip install`。

后续约束：若 `uv sync` 成功但 `uv run` 在沙箱内出现 “Unable to create process using ...\.venv\Scripts\python.exe”，不得误判为测试失败；应使用相同环境变量提升运行同一 `uv run ...` 命令完成验收，并在输出中确认测试断言结果。

### 2026-06-05：当前 Windows Go 工具链阻塞本地 Go 测试

问题：本轮真实云端联调后，本地执行 `go test ./...` 失败；进一步检查发现 `go list runtime` 也返回 `package runtime is not in std (C:\Program Files\Go\src\runtime)`。尝试 `GOTOOLCHAIN=go1.25.0` 下载并使用 `go.mod` 指定工具链后，编译标准库时出现 Go compiler internal compiler error 和访问异常。

原因：问题发生在 Go 标准库查询和工具链编译阶段，早于项目代码测试断言；判断为当前 Windows Go 安装或本机工具链环境异常，不是本项目 Go 代码回归。

解决方案：本轮保留真实云端 API 联调结果作为服务端已部署版本验证依据；本机需要重新安装或切换到干净 Go 工具链后，再恢复 `cloud-api/` 下 `go test ./...` 作为常规验收。

后续约束：后续执行 Go 测试前先运行 `go version` 和 `go list runtime` 做工具链自检；若标准库自检失败，不得把 `go test` 失败误判为项目代码失败，应先修复本机 Go 工具链或换干净环境验证。

### 2026-06-05：系统 Go 1.26.x 冷缓存编译标准库崩溃

问题：按用户要求彻底修复系统 Go 后，确认 `C:\Program Files\Go\src\runtime` 等标准库文件实际存在；在非沙箱管理员环境下 `go list runtime` 可通过，但默认系统 Go 1.26.2 执行 `go test ./...` 时在标准库 `internal/profile` 编译阶段触发 compiler internal error。升级到官方 Go 1.26.3 并清理 Go 构建缓存后，仍在标准库 `math/big`、`vendor/golang.org/x/net/idna` 编译阶段触发 compiler/runtime 崩溃。

原因：早期 `package runtime is not in std` 现象叠加了沙箱无法读取用户级 `C:\Users\3700x\AppData\Roaming\go\env` 与默认 Go 构建缓存权限问题；真正阻塞本地测试的根因是当前 Windows Go 1.26.x 工具链在本机冷缓存编译标准库时不稳定，不是项目代码回归。

解决方案：下载并验签官方 `go1.25.10.windows-amd64.msi`，通过管理员 UAC 卸载 Go 1.26.3 并安装 Go 1.25.10；安装后确认注册表显示 `Go Programming Language amd64 go1.25.10`，`go version` 返回 `go1.25.10 windows/amd64`，执行 `go clean -cache` 后 `go list runtime` 和 `cloud-api/` 下 `go test ./...` 均通过。

后续约束：本项目 `cloud-api/go.mod` 当前要求 `go 1.25.0`，Windows 本地 Go 验收优先使用官方 Go 1.25 最新补丁线；不得把 Go 1.26.x 在本机标准库冷编译时的 internal compiler error 误判为项目代码失败。系统级 Go MSI 安装/卸载必须使用管理员权限并检查 MSI 日志，静默非管理员安装可能在日志中返回 1603 且不会实际替换 `C:\Program Files\Go`。

### 2026-06-05：PySide6 授权 UI 后台任务生命周期导致完成授权后闪退

问题：真实百度授权完成后点击 UI 的“完成授权”，云端账号和密文 token 已成功入库，但 PySide6 窗口和承载 PowerShell 进程退出。

原因：授权 UI 的后台 `QRunnable` 没有由页面持有，Qt 自动删除和 Python 对象回收可能导致 worker signal 生命周期不稳定；同时账号刷新、轮询和完成授权共用同一个 `httpx.Client` 并发执行，增加了 UI 回调不确定性。

解决方案：`Worker` 改为 `setAutoDelete(False)`，页面保存 `_workers` 列表直到 `finished` 信号后再清理；真实 API 调用增加 `_api_lock` 串行化；按钮 busy 状态改为按操作局部锁定，避免账号刷新锁住设备码创建。

后续约束：PySide6 后台任务必须由 UI 对象持有到完成；跨线程信号对象不得只依赖局部变量生命周期；共享 `httpx.Client` 的真实 API 调用需要串行化或改为每个 worker 独立 client。

### 2026-06-07：pytest 默认临时目录和 C:\tmp 均可能受权限限制

问题：在 `client/` 下执行 `uv run python -m pytest` 时，pytest 默认访问 `C:\Users\3700x\AppData\Local\Temp\pytest-of-3700x` 失败；改用 `--basetemp C:\tmp\auto-backup-pytest` 后，当前沙箱仍无法创建该目录。

原因：当前运行环境对用户 Temp 目录和 `C:\tmp` 的部分目录创建/枚举存在权限限制；这发生在 pytest fixture 创建临时目录阶段，早于项目测试断言。

解决方案：继续设置 `UV_LINK_MODE=copy` 和仓库内 `UV_CACHE_DIR`，并把 `TMP`、`TEMP`、pytest `--basetemp` 都指向仓库内 `.cache/` 子目录；必要时加 `-p no:cacheprovider` 避免 `.pytest_cache` 权限噪声。本轮使用 `uv run python -m pytest -p no:cacheprovider --basetemp <repo>/.cache/pytest-basetemp-sync` 后 56 个测试通过。

后续约束：客户端 pytest 验收优先使用仓库内临时目录，例如 `$env:TMP='<repo>\.cache\tmp'; $env:TEMP='<repo>\.cache\tmp'; uv run python -m pytest -p no:cacheprovider --basetemp <repo>\.cache\pytest-basetemp`；不得把 Temp 目录权限错误误判为项目代码失败。

### 2026-06-08：P1-8 压缩阶段本机缺少 7-Zip

问题：进入 P1 阶段 8 7-Zip 加密归档与 manifest 开发时，PATH 和常见安装路径中均未发现 `7z.exe`、`7za.exe` 或 `7zr.exe`，无法执行真实压缩和 `7z t` 标准验证。

原因：当前 Windows 环境尚未安装 7-Zip；仅靠单元测试模拟 7-Zip runner 会绕过本阶段最关键的真实归档行为验收。

解决方案：从 7-Zip 官方首页指向的 GitHub release 下载 `7z2601-x64.exe` 到 `C:\tmp`，记录 SHA256 `D64A0468F5B5B0B0FC5B2188450BCD655B70809D97B1C4535F2884635094377D`；安装器 Authenticode 状态为 `NotSigned`，但来源 URL 来自 7-Zip 官方首页。通过静默安装将 7-Zip 26.01 安装到 `C:\Program Files\7-Zip\7z.exe`，并用真实 7-Zip 运行 P1-8 归档测试，测试压缩密码固定为 `Test123456789`。

后续约束：P1-8 及后续涉及压缩、验证、恢复的测试不得在本机缺少 7-Zip 时退化为模拟验收；应先安装或定位真实 `7z.exe`，必要时通过 `AUTO_BACKUP_7ZIP_PATH` 指定路径，再执行真实 `7z a`、`7z t` 和解压校验。

### 2026-06-08：P1-9 归档长路径与 pytest 临时目录约束

问题：P1 阶段 9 编排 CLI 测试中，pytest 默认长 basetemp 路径叠加 job id 和 `{archive_seq}-{archive_sha256}.7z` 文件名后，在 Windows 上出现 `FileNotFoundError`；PowerShell `Remove-Item -Recurse` 清理这些长路径测试产物时也会失败。改用 `C:\tmp` 短路径后，pytest 子进程又遇到 `PermissionError: [WinError 5] 拒绝访问`。

原因：Windows 普通路径 API 和 PowerShell 5.1 对长路径支持不稳定；当前沙箱/pytest 子进程对 `C:\tmp` 目录创建也可能受限。

解决方案：归档服务对最终 `.7z` 的哈希读取、rename 和 stat 使用 Windows `\\?\` 长路径包装；CLI 测试的短工作目录放在仓库内 `.cache/pt/`，仍保持在可写根内。

后续约束：涉及 `{archive_sha256}.7z`、深层 job cache、pytest basetemp 或清理测试产物时，优先使用仓库内短目录 `.cache/pt`、`.cache/pytest-*`；不得假定 `C:\tmp` 在 pytest 子进程中一定可写。产品代码读取或移动长 archive 路径时应继续使用长路径安全包装。

### 2026-06-08：真实全链路 staging payload 与 resumable archive 读取长路径问题

问题：补跑真实 `BackupPipeline` 全链路测试时，pytest 长 basetemp 叠加 `jobs/{job_id}/tmp/archive_000001/payload/{content_id}` 后，staging payload 复制、staging 目录删除和 `BaiduResumableUploader.file_sha256(...)` 读取最终 `.7z` 均可能出现 `FileNotFoundError`。

原因：P1-9 前一轮只修复了最终 archive 的 rename/stat 和百度上传分片读取，但 staging payload 文件名使用 64 位 `content_id`，路径深度同样会触发 Windows 普通路径 API 限制；`shutil.rmtree` 和 `Path.open()` 仍按普通路径工作。

解决方案：`archive_packager` 的 payload staging 复制、目录清理、文件存在/size 校验改用 Windows `\\?\` 路径包装；`baidu/resumable_upload.py` 的 archive SHA256 和 mtime 读取也改为长路径安全包装。

后续约束：涉及明文 manifest staging、payload staging、verify/tmp 清理、archive hash 或续传读取时，不得只修最终 `.7z` 路径；必须覆盖从源文件复查、payload 复制、临时目录删除到上传读取的整条本地文件生命周期。

### 2026-06-08：P3-14 PyInstaller 依赖下载与 CLI 入口参数

问题：进入 P3 阶段 14 打包发布时，在 `client/` 下执行 `uv add --dev 'pyinstaller>=6,<7'`，沙箱内访问清华 PyPI 镜像失败，报 `tcp connect error` 和 Windows `os error 10013`；提升权限后同一命令成功。随后检查 `uv run pyinstaller --help` 发现 PyInstaller 的 `-m` 是 Windows manifest 参数，不是 Python `-m module` 入口。

原因：当前沙箱限制联网 socket；PyInstaller CLI 与 Python 解释器 CLI 参数语义不同，不能把 `python -m auto_backup_client.app` 的入口方式直接搬到 PyInstaller 命令。

解决方案：按权限流程提升后使用 uv 正式新增 PyInstaller dev 依赖并更新 `uv.lock`；发布构建改用显式脚本入口 `client/src/auto_backup_client/app.py`，同时通过 `--paths client/src` 提供 import 路径，并用自动化测试锁定 PyInstaller 参数。

后续约束：新增或同步客户端打包依赖仍必须使用 uv；若沙箱内联网出现 `os error 10013`，按权限流程提升重跑同一 uv 命令，不得改用 pip。PyInstaller 构建入口必须使用脚本路径或 spec 文件；不得把 `-m auto_backup_client...` 当作模块入口参数传给 PyInstaller。

### 2026-06-08：PowerShell 执行策略阻止本地发布脚本

问题：从仓库根目录直接执行 `.\client_build.ps1 -DryRun` 时，PowerShell 返回 `PSSecurityException`，提示系统禁止运行脚本。

原因：当前 Windows PowerShell 会话受执行策略限制，阻止直接运行本地 `.ps1` 脚本；这发生在脚本内容执行前，不是发布脚本逻辑错误。

解决方案：使用进程级执行策略绕过运行同一脚本：`powershell -NoProfile -ExecutionPolicy Bypass -File .\client_build.ps1 -DryRun`，dry-run 成功输出 PyInstaller 命令。

后续约束：若本地 PowerShell 执行 `.ps1` 出现 `PSSecurityException`，优先用进程级 `-ExecutionPolicy Bypass -File` 复跑；不得把该错误误判为脚本语法或构建逻辑失败。

### 2026-06-08：Cloud Sync 真实性审计在沙箱内无法读取 DPAPI Device Token

问题：P3-14 云服务同步真实性审计中，沙箱内执行 `uv run python -m auto_backup_client.cloud_sync_audit_cli` 返回 `device_credential_store_error`，未能读取本机 DPAPI Device Token，因此探针尚未进入真实云端同步请求。

原因：当前沙箱无法访问或解密 `%LOCALAPPDATA%\auto_backup_bdnetdesk\credentials\device_credentials.json` 中的当前用户 DPAPI 凭据；这属于本机凭据读取权限边界，不是 Cloud Sync API 失败。

解决方案：按权限流程提升后运行同一命令，真实 `https://backup.baichengedu.com` 探针通过：`/v1/readyz` ready，首次 `POST /v1/sync/revisions` 返回 `synced`，`GET /v1/reconcile/entities/{entity_id}` 回读 `revision_id`、`data_version`、`canonical_record_sha256` 匹配，重复提交同一 revision 返回 `duplicate`。

后续约束：真实云同步审计不得只看本地 `sync_outbox` 状态；必须用云端 summary 回读匹配和重复提交 `duplicate` 证明不是虚假同步。若沙箱内出现 `device_credential_store_error`，不得误判为云服务不可用；应使用提升权限或运行时 `CLOUD_API_DEVICE_TOKEN` 重跑同一审计命令。

### 2026-06-12：Go 测试默认构建缓存受沙箱权限限制

问题：在 `cloud-api/` 下直接执行 `go test ./...` 时，Go 默认访问 `%LOCALAPPDATA%\go-build`，当前沙箱返回 `Access is denied`，导致测试在 setup 阶段失败。

原因：当前工作区写权限不覆盖用户级 Go 构建缓存目录；失败发生在 Go 编译缓存写入阶段，不是项目代码测试断言失败。

解决方案：将 `GOCACHE` 和 `GOMODCACHE` 显式指向仓库内 `.cache/go-build` 和 `.cache/go-mod` 后，`cloud-api/` 下 `go test ./...` 可在沙箱内通过。

后续约束：后续 Go 测试优先设置 `$env:GOCACHE='<repo>\.cache\go-build'; $env:GOMODCACHE='<repo>\.cache\go-mod'`，不得把用户级 Go 缓存权限错误误判为项目代码失败；`.cache/` 不得提交。

### 2026-06-13：旧 Go build cache 污染导致交叉构建误报标准库不存在

问题：执行 `.\go_build.ps1` 构建 Linux amd64 服务端二进制时，Go 1.25.10 报 `crypto/rand`、`net/url`、`log/slog`、`os/signal` 等标准库 `package ... is not in std`，路径指向 `C:\Program Files\Go\src\...`；但对应标准库目录实际存在，默认环境下 `go list` 可通过。

原因：脚本复用仓库根旧 `.cache/go-build`，该缓存状态会污染目标 `GOOS/GOARCH` 下的标准库识别；改用新的目标/批次隔离 `GOCACHE` 后，同一 Go 安装可通过目标标准库自检。

解决方案：`go_build.ps1` 新增 `-BuildId`，默认按 `yyyyMMdd-HHmmss` 生成批次目录；服务端构建产物输出到 `dist/cloud-api/<BuildId>/<goos>-<goarch>/`，并使用 `.cache/go-build/<service>/<BuildId>/<goos>-<goarch>` 作为构建缓存。构建前在目标 `GOOS/GOARCH/CGO_ENABLED` 下执行 `go list crypto/rand net/url log/slog os/signal runtime`，失败时输出 Go 环境摘要。

后续约束：发布构建不得复用长期存在的通用 `.cache/go-build` 目录；遇到 `package ... is not in std` 时，先检查 `GOCACHE` 是否被旧缓存污染，并使用目标/批次隔离缓存复跑，不得直接归因于业务代码或盲目重装 Go。

### 2026-06-13：Go 测试冷缓存并发编译标准库偶发 compiler/runtime 崩溃

问题：本轮修复后执行 `go test ./...`，在冷的批次测试缓存下编译标准库 `crypto/tls` 时触发 Go compiler/runtime 崩溃，报 `s.allocCount != s.nelems && freeIndex == s.nelems`，失败发生在项目测试断言之前。

原因：当前 Windows Go 1.25.10 本机环境在冷缓存并发编译标准库时仍可能偶发不稳定；同一代码和依赖使用低并发 `go test -p=1 ./...` 可通过。

解决方案：保持发布构建使用批次隔离 `GOCACHE`；Go 测试如遇标准库冷编译 compiler/runtime 崩溃，先用同一 `GOCACHE/GOMODCACHE` 策略配合 `go test -p=1 ./...` 复跑确认项目断言。

后续约束：不得把标准库冷编译阶段的 Go compiler/runtime 崩溃误判为项目代码失败；记录复跑命令和结果，必要时更换干净 Go 工具链继续验收。

### 2026-06-14：沙箱内 Git 暂存写入 `.git/index.lock` 被拒绝

问题：本轮提交前执行 `git add ...` 时，当前沙箱返回 `fatal: Unable to create .../.git/index.lock: Permission denied`，导致暂存失败；同时命令里一次 UTF-8 类型名手误先触发 PowerShell 类型错误。

原因：当前 workspace-write 沙箱可读 `.git`，但写入 Git index 仍可能被权限边界拒绝；失败发生在 Git 元数据写入阶段，不是工作区文件不可写，也不是提交内容问题。

解决方案：确认 `.git/index.lock` 不存在后，按权限流程提升运行同一 `git add` 和 `git commit`；提交成功后用 `git status --short --branch` 和 `git log --oneline -1` 确认结果。

后续约束：若后续暂存或提交时出现 `.git/index.lock: Permission denied`，先检查是否存在遗留 lock；不存在时按权限流程提升执行 Git 写元数据命令。不得误删 `.git` 内容或用破坏性 Git 命令绕过。

### 2026-06-16：Go 测试冷缓存下载模块受沙箱 socket 权限限制

问题：在 `cloud-api/` 下已将 `GOCACHE` 和 `GOMODCACHE` 指向仓库内 `.cache/` 后执行 `go test -p=1 ./...`，冷的 `GOMODCACHE` 仍需要从 `proxy.golang.org` 下载 `github.com/go-chi/chi/v5`、`github.com/jackc/pgx/v5` 等模块，沙箱内返回 Windows `connectex: An attempt was made to access a socket in a way forbidden by its access permissions`。

原因：仓库内缓存解决的是文件系统权限问题，但当前 sandbox 仍限制联网 socket；失败发生在 Go module 下载阶段，不是项目代码编译或测试断言失败。

解决方案：保留仓库内 `GOCACHE`/`GOMODCACHE` 设置，按权限流程提升运行同一 `go test -p=1 ./...` 命令；提升后依赖下载成功，`cloud-api/` 测试通过。

后续约束：Go 测试若在冷缓存下因 `proxy.golang.org`、DNS、socket 权限或模块下载失败，不得误判为项目代码失败；应先使用同一缓存路径提升重跑。依赖下载完成后仍应继续使用仓库内 `.cache/go-build` 和 `.cache/go-mod`，不得提交 `.cache/`。
