# Spec：v1.3 明细任务与验收

状态标记：

- `完成`：已有代码、文档或验收记录支持。
- `进行中`：已实现主体，但仍有发布候选、干净机或真实链路验收缺口。
- `待做`：尚未进入实现或仍需单独开发。
- `维护`：作为边界持续约束，非单次功能任务。

## P0 项目底座

| 任务 | 状态 | 依赖 | 边界 | 验收 |
| --- | --- | --- | --- | --- |
| 仓库初始化与授权 | 完成 | Git、LGPL-2.1 | 不提交敏感文件，不依赖全局换行策略。 | `LICENSE` 存在，README 指向新文档，Git LF 约束有效。 |
| Python 客户端骨架 | 完成 | uv、Python 3.12 | 客户端代码、测试、迁移集中在 `client/`。 | `client/pyproject.toml`、`uv.lock`、源码和测试结构存在。 |
| Go 云端服务骨架 | 完成 | Go 1.25、chi、pgx | 服务代码集中在 `cloud-api/`，入口固定。 | `cloud-api/cmd/cloud-api` 可构建，服务含 health/ready。 |
| 文档权威体系 | 完成 | 文档治理 | 新文档必须实质审计，不是形式拆分。 | `docs/current/*` 和 `docs/legacy/README.md` 完成，`git diff --check` 通过。 |
| API 与数据库契约 | 完成 | 文档治理、代码/迁移对照 | API、bridge、百度调用、SQLite/PostgreSQL schema 和同步 payload 变更前必须先对齐契约文档。 | `docs/current/api_database_contract.md` 完成，README/Tech/Agents/Audit/DCA 已纳入入口约束。 |

## P0 云端同步底座

| 任务 | 状态 | 依赖 | 边界 | 验收 |
| --- | --- | --- | --- | --- |
| SQLite `sync_outbox` | 完成 | SQLite 迁移 | 业务写入和 outbox 必须同事务。 | 本地写入失败不得产生半同步状态。 |
| Cloud Sync revision API | 完成 | Go 服务、PostgreSQL | 云端按 `entity_id + revision_id` 幂等写入。 | 重复 revision 返回 duplicate。 |
| 设备注册与 Device Token | 完成 | 稳定设备身份 | `client_version` 不参与 `device_id`。 | 同一设备不同版本设备 ID 稳定，token 可回读 current device。 |
| 云端 summary 回读 | 完成 | Revision 投影 | 真实同步验收不能只看本地状态。 | `cloud_sync_audit_cli` 首次 synced、回读匹配、重复 duplicate。 |
| schema 自检自迁移 | 完成 | 内置 migration | 正常部署不要求人工先 migrate。 | `/v1/readyz` 检查 PostgreSQL 和关键 schema。 |

## P0 百度授权与上传底座

| 任务 | 状态 | 依赖 | 边界 | 验收 |
| --- | --- | --- | --- | --- |
| 百度授权 session | 完成 | 云端 OAuth 配置 | 本项目 UI 不收集百度账号密码。 | 能创建 session，百度官方页面授权后云端入库密文 token。 |
| 密文 token 与 KDF | 完成 | DPAPI、Argon2id | 保存 KDF salt/参数，不保存密码/wrapping key。 | token-check 可用同一授权密码本地解密。 |
| 多设备账号选择 | 完成 | Device Token | 账号列表必须显示设备摘要和本机标记。 | 当前设备可选择已有账号。 |
| refresh 租约 | 完成 | token_version | 避免多设备并发覆盖 refresh token。 | 第二设备并发租约返回冲突或未获取。 |
| 百度上传核心链路 | 完成 | 真实百度 API | 固定 precreate/superfile2/create，不用模拟云端验收。 | 真实小文件和跨分片文件上传成功，并清理测试远端对象。 |
| 上传失败重试 UI | 待做 | 发布候选体验 | 现有底层支持重试状态，UI 仍需更清楚地暴露失败恢复。 | 干净 Windows 验收中失败可读、可继续、不误标 completed。 |

## P0 远端对象校对与人工修复

| 任务 | 状态 | 依赖 | 边界 | 验收 |
| --- | --- | --- | --- | --- |
| 百度 `list/listall` 校对 | 完成 | 百度 API、上传账本 | 校对对象是 SQLite 上传账本对比百度实际对象。 | 差异类型脱敏输出，递归调用限速。 |
| 只读差异报告 | 完成 | `remote_objects` | 默认不删除、不覆盖、不重传。 | 输出差异状态、对象类型、size/md5/fs_id 差异。 |
| 人工修复入口 | 完成 | 用户确认 | 只支持可审计的本地账本修复。 | `--apply --confirm APPLY_REMOTE_REPAIR` 才写库和 outbox。 |

## P1 备份主流程

| 任务 | 状态 | 依赖 | 边界 | 验收 |
| --- | --- | --- | --- | --- |
| 任务模型与来源 | 完成 | SQLite、outbox | 本地路径只存在本地 SQLite，不进同步 payload。 | 创建任务同事务写 outbox，UI 不显示完整敏感路径。 |
| 扫描与指纹 | 完成 | 本地文件系统 | 默认不跟随 symlink/junction/lnk。 | 不可读记录为 issue，不中断整个任务。 |
| 内容级去重 | 完成 | SHA256、size | 快速指纹不能作为最终去重依据。 | `content_id = sha256("v1:file:" + size + ":" + file_sha256)`。 |
| 7-Zip 归档与 manifest | 完成 | 7-Zip、cache | 明文 manifest 验证后删除。 | archive 内含 `manifest/manifest.json` 和需要的 payload。 |
| 端到端备份编排 | 完成 | 授权、上传、同步 | 按每个选择源独立归档，job.index 汇总。 | 开发机真实全链路已覆盖，干净机仍需复验。 |
| 长路径硬化 | 完成 | Windows 路径包装 | 不能只修最终 archive，要覆盖 staging/upload/restore。 | 长路径测试不再 FileNotFound。 |

## P2 数据治理与恢复

| 任务 | 状态 | 依赖 | 边界 | 验收 |
| --- | --- | --- | --- | --- |
| 缓存额度与 artifact 管理 | 完成 | cache_artifacts | 有效预算低于 40GiB 禁止新任务。 | 清理等级和 artifact 生命周期可查询。 |
| 来源映射 UI | 完成 | 设备历史、SQLite | 普通用户不需要输入内部 job_id。 | 默认拉取本机历史，任务下拉选择最近记录。 |
| 校对与同步 UI | 完成 | 远端校对、Cloud Sync | 明确 SQLite/Baidu/Cloud Sync 口径。 | summary 回读可见，不泄露敏感信息。 |
| 原始数据清理 | 完成 | 清理候选、回收站 | 只由用户手动触发，默认回收站。 | 源文件身份变化时禁止清理。 |
| 来源级恢复 | 完成 | archive、manifest、百度下载 | 候选以备份来源为粒度，覆盖默认关闭。 | 本地/远端 archive 恢复后 SHA256 复验通过。 |
| 覆盖恢复 | 待做 | 回收站保护设计 | 不能直接覆盖。 | 若实现，必须覆盖前移入回收站并新增验收矩阵。 |

## P3 发布与最终验收

| 任务 | 状态 | 依赖 | 边界 | 验收 |
| --- | --- | --- | --- | --- |
| PyInstaller onedir 构建 | 进行中 | pywebview、webui、migrations | 当前不是最终安装器。 | dry-run 和开发机构建通过，干净机启动待验收。 |
| pywebview UI 替换 | 进行中 | WebView2 | 不再维护 PySide6 UI。 | 开发机自动化覆盖，干净 Windows UI 流程待验收。 |
| 发布候选体验修复 | 进行中 | 用户反馈 | 修复入口、授权、设备 ID、历史同步、最近任务设备作用域、操作可追踪性和任务候选选择等阻塞。 | 开发机已补齐本机/全局任务区分、本机 running 继续入口、最近操作失败原因展示、恢复/清理/校对任务筛选入口，仍需 R04-R14 真实验收。 |
| 干净 Windows R04 首次启动 | 待做；开发机预检入口已补齐 | 发布包、WebView2 | 不要求已有 Device Token；预检不能替代真实双击启动。 | UI 启动，可自动注册或提示真实云端配置问题；`release_candidate_preflight` 检查发布目录启动资源和 WebView2 可见性。 |
| R05 百度授权 | 待做 | 真实云端、百度官方页面 | 百度账号密码只在百度官方页面输入。 | DPAPI 保存 Device Token/KDF，仓库无敏感落盘。 |
| R06 真实备份 | 待做 | 真实百度账号、缓存、7-Zip | 不闪 CMD，不泄露密码/token/完整路径。 | 完成扫描、归档、上传、同步、校对和 final sync。 |
| R07 校对 | 待做；开发机 summary 回读入口已补齐 | 本机历史、百度 list/listall | 普通用户不输入内部 ID；实体 ID 回读只作为诊断入口。 | 来源映射、远端校对、summary 回读都可用。 |
| R08 清理 | 待做 | 完成任务、源文件未变 | 默认回收站，永久删除隐藏。 | 清理记录写本地和 outbox。 |
| R09 恢复 | 待做 | 本地或远端 archive | 来源级恢复，默认保留两者。 | 本地/远端恢复后 SHA256 一致。 |
| R10 断网补偿 | 待做 | 本地主库、sync worker | 云端断网时本地任务继续。 | 网络恢复后 outbox 补偿同步。 |
| R11 升级 | 待做；开发机预检入口已补齐 | DPAPI 凭据、SQLite 迁移 | 稳定 `device_id` 不能变；预检只确认迁移打包和目录边界。 | 新包能读取旧凭据和历史；`release_candidate_preflight` 确认 SQLite migration 已随包发布。 |
| R12 卸载/清理 | 待做；开发机预检入口已补齐 | 发布目录、用户数据目录 | 删除程序不等于删除用户数据；默认用户数据不得写入程序目录。 | 程序目录和用户数据边界清晰；默认 data/cache 位于 `%LOCALAPPDATA%\auto_backup_bdnetdesk\`，预检拒绝发布目录混入运行期数据。 |
| R13 云同步真实性审计 | 待做干净机复验；开发机 bridge/UI 回读入口已补齐 | 真实云端 | 不能只看本地 outbox。 | summary matched、duplicate verified。 |
| R14 敏感信息审计 | 进行中 | dist、日志、SQLite、UI | 无敏感文件和敏感输出；自动化入口只能证明指定输入，不替代干净机实际产物审计。 | `release_sensitive_audit` 开发机入口已补齐；干净机 dist/log/UI/outbox 仍需执行无 token/password/key/manifest 明文验收。 |

## Spec 审计结论

旧排期以阶段流水为主，适合记录历史，但不适合指导下一位开发者执行任务。新 spec 明确：

- 哪些能力已经完成但仍要维护边界。
- 哪些能力主体完成但发布验收仍没完成。
- 哪些能力明确待做，不能被“基本完成”字样掩盖。
- 每个任务的不能做项和验收入口。
