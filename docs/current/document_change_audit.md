# Document Change Audit：新旧文档修正记录

本文件记录新文档相比旧文档修正了哪些口径。后续代码变更、测试变更或发布验收应与本表对账，防止文档先行后产生实现偏差。

| 编号 | 旧文档原口径或风险 | 新文档修正口径 | 修正原因 | 代码/测试/Git 依据 | 后续校对点 |
| --- | --- | --- | --- | --- | --- |
| DCA-001 | `docs/product_spec_v1.3.md` 同时承载 PRD、技术栈、UI 页面、数据库契约、状态机和验收标准。 | 拆为 `prd.md`、`design.md`、`tech.md`、`spec.md`。 | 单文件过长且职责混杂，导致开发者难以判断权威边界。 | 当前规格文件约 38KB；用户明确要求细化为 PRD/Design/Tech/Spec。 | 后续功能变更必须同步对应分层文档。 |
| DCA-002 | 旧文档仍保留 PySide6 阶段描述，容易误判当前 UI 技术栈。 | 当前 UI 固定为 pywebview + WebView2 + 原生静态 HTML/CSS/JS。 | `fac434d` 已删除 PySide6 UI 并新增 pywebview 工作台。 | `fac434d 实现pywebview静态工作台替换PySide6界面`。 | 检查 README、用户手册和发布文档不再把 PySide6 当当前 UI。 |
| DCA-003 | `roadmap_progress.md` 是长历史流水，容易被当作 spec 使用。 | `roadmap_progress.md` 只记录进度和接力；任务执行以 `spec.md` 为准。 | 历史记录适合追溯，不适合判断当前边界。 | `roadmap_progress.md` 超过 190KB，包含大量完成记录。 | 每次新阶段仍更新 roadmap，但任务边界同步到 spec。 |
| DCA-004 | “基本完成”“进行中”未区分代码完成和发布可交付。 | `spec.md` 明确 P3-14 仍未完成，R04-R14 是发布硬门槛。 | 防止把开发机自动化通过误判为 v1.3 可发布。 | `docs/release_acceptance_matrix.md` R04-R14 多项待执行。 | 干净 Windows 验收完成后更新 spec 和 release matrix。 |
| DCA-005 | 百度授权、上传、云同步的 mock/真实验收边界分散。 | 云端/百度契约必须真实 API 验收；mock 只用于纯本地逻辑单测。 | 项目要求真实云端 API 和真实百度 API 为联调依据。 | `AGENTS.MD` 真实云端 API 联调约束；真实批测提交记录。 | 新增相关功能时必须记录官方文档或真实联调依据。 |
| DCA-006 | 设备 ID 曾受 client version 或摘要展示影响，用户难以对照云端设备。 | `device_id` 由本机固定特征派生，client version 不参与；UI 使用前后缀摘要。 | 防止同一设备升级后产生不同身份，影响授权、恢复和历史同步。 | `f58ee67`、`fabc6ec`。 | 干净机升级验收 R11 必须验证设备身份稳定。 |
| DCA-007 | Cloud Sync 验收容易只看本地 `sync_outbox`。 | 必须用真实云端 summary 回读匹配和 duplicate 验证。 | 防止“本地假同步”。 | `9a798ea 完成云同步真实性审计`；`cloud_sync_audit_cli`。 | R13 干净机复验保留输出摘要。 |
| DCA-008 | 备份来源和恢复候选粒度曾被 UI 误导为文件夹内部单文件。 | 主流程按用户选择来源独立归档和来源级恢复。 | 与 P3-14 多来源备份和恢复结构修复一致。 | `63ca202`、`bc3fe67`。 | R09 验证文件夹来源恢复保留根目录结构。 |
| DCA-009 | 清理能力可能被误解为自动清理或远端删除。 | 原始数据清理只由用户手动触发，默认回收站，不自动删除远端对象。 | 防止数据丢失。 | `source_cleanup.py`、P2-12 完成记录、用户手册。 | R08 验证回收站和永久删除高级确认。 |
| DCA-010 | 旧文档中密码、token、路径、manifest 的安全边界散落。 | PRD/Design/Tech/Agents 集中写清敏感信息保存和展示规则。 | 敏感边界必须在开发入口可见。 | `redaction.py`、`device_credentials.py`、`baidu/kdf_store.py`、AGENTS 敏感信息规则。 | R14 审计 dist、日志、SQLite outbox 和 UI 输出。 |
| DCA-011 | 发布构建口径分散在 README、client README、release matrix。 | `tech.md` 提供构建和测试命令，release matrix 保留发布验收细节。 | 减少命令漂移和入口重复。 | `client_build.ps1`、`go_build.ps1`、`release_build.py`。 | 构建脚本变化时同步 tech 和 release matrix。 |
| DCA-012 | 旧文档没有专门记录“新文档修正了旧文档什么”。 | 新增本文件作为文档变更审计表。 | 用户要求后续能与实际代码变更校对，防止偏差。 | 本次文档治理需求。 | 每次修正文档口径必须追加 DCA 记录。 |
| DCA-013 | 云端 HTTP API、pywebview bridge API、百度 API 参数、SQLite/PostgreSQL schema 和同步 payload 散在代码、迁移、旧规格、README 和验收记录中。 | 新增 `docs/current/api_database_contract.md` 作为接口、传参、调用方式、成功返回、错误返回、典型示例、数据库结构、同步字段和变更检查清单的单一对照契约。 | 用户明确指出缺少统一技术契约会导致后续修复、新增和联调对齐错误。 | `cloud-api/internal/cloudapi/server.go`、`types.go`、`baidu_types.go`；`client/src/auto_backup_client/webview_bridge.py`、`baidu/cloud_api.py`、`baidu/upload.py`、`sqlite_store.py`；`client/migrations/sqlite/*.sql`；`cloud-api/migrations/postgres/*.sql`；`docs/baidu_netdisk_openapi_reference.md`。 | 后续 API、数据库、同步 payload 或版本字段变更必须优先更新该契约文档，并同步 Tech/Agents/Audit。 |
| DCA-014 | `client/docs/frontend_spec_pywebview.md` 中部分 bridge 签名仍按旧设想写为显式传 `session_id`，例如 `poll_baidu_authorization(session_id)` 和 `complete_baidu_authorization(session_id, authorization_password)`。 | 当前真实 bridge 契约为 `poll_baidu_authorization()` 和 `complete_baidu_authorization(password)`，session ID 由 bridge 内部 `_auth_session_id` 保存；本轮已同步修正前端 spec，并在 `api_database_contract.md` 固化。 | 前端 spec 与实现漂移，继续按旧签名开发会触发 UI 调用错误。 | `webview_bridge.py` 真实方法签名；`client/src/auto_backup_client/webui/js/api.js` 调用包络；本轮 `client/docs/frontend_spec_pywebview.md` 修正。 | 后续 bridge/API 签名变化必须先改契约文档，再改前端 spec、调用方和测试。 |
| DCA-015 | 云端同步 summary 的 bridge DTO 缺少统一约束，`webview_bridge.py#get_cloud_sync_summary` 曾引用 `EntitySummary` 中不存在的 `revision_count/latest_*` 字段。 | 契约文档明确 `get_cloud_sync_summary(entity_id)` 应对齐 `baidu.models.EntitySummary`：`entity_id/entity_type/data_version/revision_id/canonical_record_sha256/updated_by_device_id/recent_revisions`；2026-06-22 代码已对齐并补齐校对与同步页回读入口。 | 防止云端同步页面在运行时因 DTO 字段不匹配失败，也让 R13 真实复验有明确入口。 | `client/src/auto_backup_client/baidu/models.py`、`webview_bridge.py`、`cloud-api/internal/cloudapi/types.go`、`client/tests/test_webview_bridge.py`。 | R13 干净机复验时继续用真实云端 summary 回读和 duplicate 结果证明同步真实性。 |
| DCA-016 | 最近任务曾把本机任务、其他设备任务和云端历史混在同一展示口径中，且 `source_count` 可能按已导入来源行数显示为 0；前端也曾让 `running` 本机任务不可继续。 | 最近任务和备份任务 DTO 必须返回 `scope/scope_label/owner_device_hint/current_device/can_*`；本机任务可按状态继续，全局任务只读；`source_count` 使用任务记录持久化值，`local_source_count` 仅作诊断。 | 多设备场景下用户必须能区分本机可执行任务和全局历史任务，避免把其他设备 running 状态误当成本机死任务，也避免云端历史部分导入时显示“0 个来源”。 | `webview_bridge.py`、`webui/js/views/dashboard.js`、`webui/js/views/jobs.js`、`client/tests/test_webview_bridge.py`、`client/tests/test_webui_static.py`。 | R06/R07/R10 干净机复验需确认同一账号多设备历史中，本机任务可继续，其他设备任务只读且显示设备摘要和正确来源数。 |
| DCA-017 | 最近操作和长操作 DTO 曾只暴露 `kind/stage/message`，前端失败时只能看到“操作失败”；恢复、清理、校对页也缺少任务选择入口，用户不知道从哪个任务恢复/清理/校对。 | Operation DTO 必须提供 `kind_label/operation_id_hint/context` 等脱敏上下文；最近操作必须展示任务或候选范围、阶段和失败原因；恢复、清理、校对页必须通过 `list_job_choices()` 提供任务下拉并按任务筛选候选或来源映射。 | 发布候选 UI 需要让普通用户知道“继续任务在哪里”“失败的是哪个任务和为什么”“恢复/清理/校对作用于哪个任务和来源”，否则 R06-R09 即使底层能力存在也无法验收。 | `webview_bridge.py`、`webui/js/render.js`、`webui/js/views/dashboard.js`、`jobs.js`、`restore.js`、`cleanup.js`、`reconcile.js`、`client/tests/test_webview_bridge.py`、`client/tests/test_webui_static.py`。 | R06/R07/R08/R09 干净机复验需确认继续任务状态在本页可见，最近操作失败原因可读，恢复/清理/校对可从任务下拉选择并显示候选阻塞原因。 |

## 使用方式

后续审计文档与代码偏差时，按以下顺序：

1. 找到相关 DCA 编号。
2. 查看新口径和依据。
3. 对照代码、测试、Git 提交和发布验收结果。
4. 若代码未满足新口径，在 `spec.md` 标记待做或进行中。
5. 若新口径不再正确，更新对应权威文档并追加新的 DCA 记录，不覆盖旧记录。
