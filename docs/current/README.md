# 当前权威文档入口

本目录是 v1.3 后续开发的权威文档入口。旧文档继续保留，但默认只作为历史依据、外部 API 依据、验收记录或补充说明；当旧文档与本目录冲突时，以本目录为准，并在 `document_change_audit.md` 中追踪修正原因。

## 文档权威顺序

| 顺序 | 文档 | 用途 |
| --- | --- | --- |
| 1 | `docs/current/README.md` | 判断该读哪些文档、哪些旧文档仍可参考。 |
| 2 | `docs/current/prd.md` | 产品目标、用户流程、支持/不支持范围和产品验收。 |
| 3 | `docs/current/spec.md` | 全 v1.3 任务拆解、状态、依赖、边界和验收标准。 |
| 4 | `docs/current/design.md` | pywebview 前端设计、页面规范、交互与展示边界。 |
| 5 | `docs/current/tech.md` | 技术栈、架构、API、数据库、凭据、缓存、构建和测试约束。 |
| 6 | `docs/current/agents.md` | 前后端开发规范、测试命令、提交和文档维护规则。 |
| 7 | `docs/current/audit.md` | 实质审计结论、旧文档冲突、风险和待验证问题。 |
| 8 | `docs/current/document_change_audit.md` | 新旧文档口径差异，用于后续与代码和测试变更校对。 |

## 开发前阅读路径

- 产品功能变更：先读 `prd.md`，再读 `spec.md` 对应任务，最后读 `tech.md` 中相关技术边界。
- UI 或交互变更：先读 `design.md`，再读 `spec.md` 对应任务和 `agents.md` 前端规范。
- 云端、百度 API、SQLite、凭据、同步、上传、恢复或发布变更：先读 `tech.md`，再读 `spec.md`，必要时查 `docs/baidu_netdisk_openapi_reference.md` 或部署文档。
- 真实联调、发布验收或干净机测试：先读 `spec.md` 的 P3-14，再读 `docs/release_acceptance_matrix.md`。
- 修改文档本身：必须同时维护 `audit.md` 或 `document_change_audit.md` 中对应的审计记录。

## 当前项目状态

当前项目处于 P3 阶段 14 打包发布与最终验收。代码已经具备 pywebview 工作台、真实百度授权、真实百度上传、SQLite 上传账本、断点续传、Cloud Sync、远端校对、缓存治理、原始数据清理和来源级恢复等核心能力。

尚未完成完整 v1.3 发布。剩余主线是干净 Windows R04-R14 验收，包括首次启动、授权、真实备份、校对、清理、恢复、断网补偿、升级、卸载和敏感信息审计。

## 旧文档状态

旧文档不删除。保留原因是项目需要历史提交依据、官方 API 离线记录、用户手册、发布验收矩阵和已沉淀问题。旧文档状态见 `docs/legacy/README.md`。

重要约束：

- `docs/product_spec_v1.3.md` 是历史整合规格，不再承担唯一权威职责。
- `docs/roadmap_progress.md` 仍是进度和提交接力记录，不再作为 PRD 或技术设计正文使用。
- `client/README.md` 只保留客户端入口和常用命令，详细规范以本目录为准。
- 外部 API 口径仍必须优先查官方文档；离线记录只作为已获取依据和本项目实现边界说明。

## 文档维护原则

- 新增或修改功能时，先更新 `prd.md` 和 `spec.md`，再改代码。
- 涉及 UI 表现时，同步更新 `design.md`。
- 涉及技术栈、API、数据结构、凭据、部署或测试时，同步更新 `tech.md`。
- 如果新文档修正了旧文档口径，必须在 `document_change_audit.md` 增加记录。
- 如果发现旧文档模糊、冲突或与代码不一致，必须在 `audit.md` 记录处理结果。
