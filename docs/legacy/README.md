# 旧文档与参考资料索引

本目录索引旧文档和额外功能文档。旧文档保留用于历史追溯、官方依据、发布验收和用户说明；当前开发默认以 `docs/current/` 为权威入口。

## 状态说明

| 状态 | 含义 |
| --- | --- |
| 当前权威 | 仍是当前对应主题的权威来源。 |
| 参考依据 | 可作为官方 API、部署或手动验收依据，但不能覆盖 `docs/current/` 口径。 |
| 历史快照 | 记录旧阶段方案或历史进度，不再作为当前开发权威。 |
| 发布验收 | 用于 P3-14 发布候选验收，仍需维护。 |
| 用户文档 | 面向普通用户和测试人员，随发布状态更新。 |

## 文档索引

| 文档 | 状态 | 说明 | 当前权威替代 |
| --- | --- | --- | --- |
| `docs/product_spec_v1.3.md` | 历史快照 | 早期完整方案，混合 PRD、技术、UI、数据契约和验收。 | `docs/current/prd.md`、`tech.md`、`spec.md` |
| `docs/ui_design_pywebview.md` | 历史快照 | pywebview UI 重构设计的原始设计文档。 | `docs/current/design.md` |
| `client/docs/frontend_spec_pywebview.md` | 历史快照 | pywebview 前端实现规格原稿。 | `docs/current/design.md`、`tech.md`、`spec.md` |
| `docs/roadmap_progress.md` | 当前权威 | 只作为进度、排期变更和提交接力记录。 | 功能边界看 `docs/current/spec.md` |
| `docs/release_acceptance_matrix.md` | 发布验收 | P3-14 R01-R14 发布验收矩阵，仍需维护。 | 与 `docs/current/spec.md` P3-14 对齐 |
| `docs/user_guide.md` | 用户文档 | 普通 Windows 用户和测试人员说明。 | 产品边界看 `docs/current/prd.md` |
| `docs/baidu_netdisk_openapi_reference.md` | 参考依据 | 百度网盘开放平台接口离线参考和获取记录。 | 技术边界看 `docs/current/tech.md` |
| `docs/deployment_nginx_backup_baichengedu.md` | 参考依据 | `backup.baichengedu.com` 部署和 nginx 反代说明。 | 技术边界看 `docs/current/tech.md` |
| `client/docs/baidu_auth_manual_validation.md` | 参考依据 | 百度授权和 KDF/Device Token 手动验收说明。 | 授权产品边界看 `prd.md`，技术边界看 `tech.md` |
| `README.md` | 当前入口 | 项目入口、当前状态和常用命令。 | 详细规范看 `docs/current/` |
| `client/README.md` | 当前入口 | 客户端入口和常用命令。 | 详细规范看 `docs/current/` |
| `AGENTS.MD` | 当前权威 | 项目通用协作规则和沉淀问题。 | 开发细则看 `docs/current/agents.md` |

## 维护规则

- 不删除旧文档，除非用户明确要求且已确认没有历史追溯价值。
- 旧文档若与 `docs/current/` 冲突，以 `docs/current/` 为准。
- 旧文档口径被新文档修正时，在 `docs/current/document_change_audit.md` 记录。
- 发布验收矩阵和用户手册仍需随真实发布状态更新。
