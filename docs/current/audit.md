# Audit：文档实质审计结论

## 审计范围

本次审计覆盖：

- `docs/product_spec_v1.3.md`
- `docs/ui_design_pywebview.md`
- `client/docs/frontend_spec_pywebview.md`
- `docs/release_acceptance_matrix.md`
- `docs/user_guide.md`
- `docs/roadmap_progress.md`
- `README.md`
- `client/README.md`
- 根 `AGENTS.MD`
- 近期 Git 提交：pywebview 替换、设备 ID、授权、发布候选修复相关提交。

## 总体结论

旧文档的问题不是“信息不足”，而是“信息挤在一起”。同一份文档同时承担 PRD、技术设计、数据契约、UI 设计、任务 spec、发布验收、历史进度和协作规范，导致后续开发很难判断：

- 哪些是产品需求。
- 哪些是技术实现细节。
- 哪些是历史记录。
- 哪些是当前权威边界。
- 哪些能力已经完成但仍需发布验收。

本次新增 `docs/current/` 后，开发入口按职责拆分，并对旧口径中的模糊点做显式修正。

## 已澄清边界

| 主题 | 旧风险 | 新口径 |
| --- | --- | --- |
| 文档权威 | `product_spec_v1.3.md` 既像 PRD 又像 tech/spec。 | `prd.md`、`design.md`、`tech.md`、`spec.md` 分工明确。 |
| API 与数据库契约 | 云端 API、bridge API、百度 API 参数和 SQLite/PostgreSQL 表结构散在代码、迁移、旧规格和 README 中。 | `api_database_contract.md` 作为接口、返回、错误、示例、schema、同步 payload 和版本字段的单一对照文档。 |
| UI 技术栈 | 旧记录仍混有 PySide6 阶段历史。 | 当前 v1.3 UI 为 pywebview + 原生静态 Web UI。 |
| 发布状态 | “基本完成”“进行中”容易被误读为可发布。 | P3-14 仍未完成，干净 Windows R04-R14 是发布前硬门槛。 |
| 设备 ID | 曾出现摘要口径不清和版本变化风险。 | `device_id` 由本机固定特征派生，client version 不参与。 |
| 云同步验收 | 旧记录中容易只看本地 outbox。 | 必须用真实云端 summary 回读匹配和 duplicate 验证。 |
| 百度联调 | 单元测试可 mock 的边界不够突出。 | 授权、上传、删除、同步契约必须真实 API 验收。 |
| 恢复粒度 | 容易被理解为文件夹内部单文件主流程。 | 主流程按用户选择来源恢复；文件夹来源保留根结构。 |
| 清理边界 | 清理和恢复/删除远端容易混淆。 | 清理只由用户手动触发，默认回收站，不自动删除远端。 |
| 密码和凭据 | 分散在多文档中。 | 安全边界集中写入 PRD/Tech/Design/Agents。 |

## 需求完整性检查

| 能力 | PRD 入口 | Spec 入口 | 审计结论 |
| --- | --- | --- | --- |
| 授权 | 首次启动与授权 | P0 百度授权与上传底座 | 已覆盖，需干净机 R05。 |
| 备份 | 新建备份 | P1 备份主流程、P3 R06 | 已覆盖，需干净机真实复验。 |
| 上传 | 百度 API 边界 | P0 上传核心链路、P3 R06 | 已覆盖，真实 API 为验收依据。 |
| 同步 | 校对与同步 | P0 云端同步底座、P3 R13 | 已覆盖，summary 回读是硬标准。 |
| 校对 | 校对与同步 | P0 远端校对、P3 R07 | 已覆盖，禁止自动危险修复。 |
| 清理 | 原始数据清理 | P2 清理、P3 R08 | 已覆盖，默认回收站。 |
| 恢复 | 恢复 | P2 恢复、P3 R09 | 已覆盖，覆盖恢复待做。 |
| 发布 | 产品验收 | P3 R04-R14 | 已覆盖，当前最大剩余缺口。 |

## 开发可执行性检查

新 spec 对每个任务补齐：

- 状态。
- 依赖。
- 边界。
- 验收。

这比旧的排期流水更适合接力开发。旧 `roadmap_progress.md` 继续记录“发生过什么”，新 `spec.md` 负责“接下来按什么做”。

## 技术一致性检查

已对齐近期提交事实：

- `fac434d` 已实现 pywebview 静态工作台替换 PySide6。
- `f58ee67` 修复稳定 Device ID。
- `fabc6ec` 修复本机设备摘要与授权密码验证入口。
- P3-14 多轮修复已覆盖发布候选体验，但干净 Windows R04-R14 仍待执行。

新文档不再把 PySide6 当作当前 UI 实现；只在历史问题和旧文档索引中保留 PySide6 阶段记录。

本轮 API/数据库契约补强对齐了以下真实来源：

- Go `/v1` 路由、请求/响应类型和错误包络来自 `cloud-api/internal/cloudapi/server.go`、`types.go` 和 `baidu_types.go`。
- SQLite schema 和同步字段来自 `client/migrations/sqlite/*.sql` 和 `sqlite_store.py`。
- PostgreSQL schema、revision 投影和 schema readiness 来自 `cloud-api/migrations/postgres/*.sql`、`postgres_store.go` 和 `index_payload.go`。
- pywebview bridge 方法、返回包络和 operation DTO 来自 `webview_bridge.py` 与前端 `api.js`。
- 百度 API 调用口径来自 `docs/baidu_netdisk_openapi_reference.md` 和 `baidu/upload.py`；2026-06-17 本轮重新通过提升后的 `curl.exe` 获取到官方“预上传”页面 HTML。

本轮同步修正了 `client/docs/frontend_spec_pywebview.md` 中 `poll_baidu_authorization(session_id)`、`complete_baidu_authorization(session_id, authorization_password)` 和 operation 状态枚举等已确认过时签名。2026-06-22 已完成代码级校对：`webview_bridge.py#get_cloud_sync_summary` 不再引用 `EntitySummary` 中不存在的 `revision_count/latest_*` 字段，校对与同步页已补齐 Cloud Sync summary 回读入口；干净 Windows R13 仍需真实云端复验。

## 安全边界检查

已集中明确：

- Device Token 只存在 DPAPI/运行时，不进入仓库。
- 百度 token 只保存云端密文 envelope，客户端本地运行时解密。
- 授权密码和归档密码不持久化。
- KDF store 只保存 salt 和参数。
- 明文 manifest 只短暂存在缓存临时目录。
- UI/日志/outbox 不输出完整敏感路径、token、密码、key 或 manifest 明文。

## 文档美观性检查

新文档采用：

- 短段落。
- 表格。
- 状态标签。
- 明确的“支持/不支持/待做”。
- 命令块。
- 审计表。

目标是让开发者快速扫描，而不是从长篇历史文本中找隐含边界。

## 仍需后续验证

- 干净 Windows R04-R14 全矩阵尚未执行。
- 最终安装器尚未完成，当前是 onedir 发布目录。
- 覆盖恢复仍未实现，若需要必须单独设计回收站保护。
- 上传失败重试 UI 仍可进一步细化。
- pywebview bridge 的云端同步 summary DTO 字段需要按 `api_database_contract.md` 做代码级对齐。
- 旧文档中历史完成记录很多，后续若发现与代码不一致，应继续补充 `document_change_audit.md`。
