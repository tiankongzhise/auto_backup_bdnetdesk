# Agents：前后端开发规范

本文件补充根 `AGENTS.MD`。根文件仍是项目通用协作规则和问题沉淀入口；本文件只聚焦当前代码开发和文档维护执行规范。

## 开发前必读

每次开始开发前按顺序阅读：

1. `AGENTS.MD`
2. `docs/current/README.md`
3. `docs/roadmap_progress.md`
4. 与任务相关的 `prd.md`、`design.md`、`tech.md`、`api_database_contract.md`、`spec.md`
5. Git 最近 20 条提交
6. 相关关键提交 `git show --stat --summary --format=fuller <commit>`

不得只依据当前文件快照判断项目状态。

## 文档先行

新增或修改功能前：

- 先在 `docs/roadmap_progress.md` 写当前工作项、计划范围和验收标准。
- 如改变产品行为，更新 `docs/current/prd.md`。
- 如改变 UI，更新 `docs/current/design.md`。
- 如改变技术栈、凭据、构建、测试或部署，更新 `docs/current/tech.md`。
- 如改变云端 HTTP API、pywebview bridge API、百度 API 调用、SQLite/PostgreSQL schema、同步 payload、版本字段或 canonical hash 过滤规则，先更新 `docs/current/api_database_contract.md`。
- 如改变任务状态或验收口径，更新 `docs/current/spec.md`。
- 如修正旧文档口径，更新 `docs/current/document_change_audit.md`。

## Python 客户端规范

- 代码放在 `client/src/auto_backup_client/`。
- 测试放在 `client/tests/`。
- SQLite 迁移放在 `client/migrations/sqlite/`。
- 依赖只使用 uv 管理；新增依赖用 `uv add`，移除依赖用 `uv remove`。
- 不得使用 `pip install` 改变客户端依赖。
- 本地路径、密码、token、wrapping key 和 manifest 明文不得进入日志、UI、sync payload 或测试快照。
- 真实联调用 DPAPI 或运行时环境变量提供敏感凭据，不写仓库文件。

## pywebview 前端规范

- 静态前端放在 `client/src/auto_backup_client/webui/`。
- `api.js` 是唯一直接访问 `window.pywebview.api` 的模块。
- 不使用 localStorage/sessionStorage。
- 不使用未清洗数据写 `innerHTML`。
- 密码输入提交后立即清空。
- UI 只能展示脱敏 DTO。
- 长操作通过 `operation_id` 轮询，不能重复触发写动作。
- 写操作必须通过 Python bridge 串行化。

## Go 云端规范

- 服务代码放在 `cloud-api/`。
- 入口固定为 `cloud-api/cmd/cloud-api`。
- PostgreSQL 迁移放在 `cloud-api/migrations/postgres/` 并嵌入二进制。
- 正常部署依赖 `serve` 启动自检自迁移，不要求人工先 migrate。
- `migrate` 命令只作为排障或人工修复入口。
- 日志输出必须脱敏，不输出 DSN、密码、token 或完整敏感 payload。

## 测试规范

文档-only：

```powershell
git diff --check
```

客户端：

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

Go：

```powershell
cd cloud-api
$env:GOCACHE='..\.cache\go-build'
$env:GOMODCACHE='..\.cache\go-mod'
go test -p=1 ./...
```

真实联调：

- 云端/百度契约必须走真实云端或真实百度 API。
- Cloud Sync 必须用 summary 回读匹配证明。
- 百度上传必须真实上传临时文件并清理远端对象。

## 提交规范

- 每个小功能一次 commit。
- commit message 使用中文。
- message 说明技术细节和项目变化。
- 提交前更新 `docs/roadmap_progress.md` 完成记录、提交摘要和后续待办。
- 不把无关功能塞进同一提交。
- 不提交 `.env`、SQLite、缓存、日志、构建产物、token、密码或密钥。
- 完成后向用户汇报时，必须明确说明本次是否修改 Go 服务端、是否需要重新编译并部署 Go 服务端，还是只需要重新编译/打包 client。
- 如涉及 Go 服务端配置文件、`.env.example`、环境变量读取逻辑、部署文档中的环境变量或启动参数，必须额外提示用户同步检查服务器配置文件和 env 环境变量。
- 如只改客户端、前端、客户端测试或客户端文档，必须明确说明不需要重新编译/部署 Go 服务端；如只是文档规则变更，则说明 Go 服务端和 client 都无需重新编译。

## 文档审计规范

发现旧文档与当前权威口径冲突时：

1. 在新权威文档中写清正确口径。
2. 在 `audit.md` 记录冲突和处理结论。
3. 在 `document_change_audit.md` 写旧口径、新口径、修正原因、代码/测试/Git 依据和后续校对点。
4. 需要时在旧文档顶部加状态提示。

不得为了“看起来一致”删除历史记录。历史记录用于追溯，权威口径用于开发。
