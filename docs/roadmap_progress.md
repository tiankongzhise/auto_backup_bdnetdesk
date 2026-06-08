# 开发排期与进度

本文件用于记录项目开发排期、当前进度和提交完成情况。每次开发开始前必须更新当前工作项、计划修改范围和验收标准；每个小功能完成后，必须先更新完成记录、提交摘要和后续待办，再将本文件与代码和文档一起纳入同一个 commit。

## 当前阶段

2026-06-08 进度审计结论：项目已经完成云端同步服务、真实云端部署联调、百度授权与账号选择、本机 Device Token/KDF 凭据、百度上传核心链路、本地 SQLite 上传账本、`uploadid` 断点续传、`.meta.json`/`job.index.json` 生成、`sync_outbox` worker、脱敏 `sync-outbox` CLI、百度 `list/listall` 客户端列表能力、远端对象校对与人工修复入口、备份任务主 UI 与任务模型、扫描与内容指纹、内容去重索引、7-Zip AES-256 加密归档与 manifest、端到端备份编排、真实百度全链路验收、Windows 长路径文件访问硬化、缓存额度与 artifact 生命周期管理、来源映射和远端校对 UI、原始数据清理服务与 UI。当前已经完成 P2 阶段 12，尚未进入完整桌面端备份产品可发布状态。

离完整完成开发计划的主要缺口：恢复流程、打包发布和端到端验收。按产品规格 v1.3 的阶段拆分估算，离可交付仍约剩 15%-20%；最大风险集中在恢复正确性、恢复冲突策略和最终打包验收。

## 当前工作项

- P2 阶段 12 原始数据清理开发完成；下一个开发阶段为 P2 阶段 13 恢复流程。
- 本轮属于主排期 P2 阶段 12，不改变阶段顺序；已实现手动触发的原始数据清理候选、清理前复查、回收站/隔离目录/高级永久删除入口和版本化清理记录。
- 本轮已新增 `source_cleanup_records` 同步实体、文件身份复查、原始数据清理服务和 PySide6 原始数据清理页。
- 本阶段未新增 Go API、未修改云端 schema、未实现恢复流程、未实现自动定时清理；云端同步仍通过现有 `sync_outbox` revision ingest 表达 `source_cleanup_records`。
- 下一阶段开始前必须把“本次开发阶段：P2 阶段 13 恢复流程”写入当前工作项，并同步计划修改范围和验收标准。

## 本次验收标准

- P2-12 已验收：只有 `job_status=completed`、archive 标准验证通过、upload session `remote_created`、`.meta.json`/`job.index.json` 已上传且本地记录完整的文件才进入清理候选；云端同步待同步时允许清理但 UI 显示提示。
- P2-12 已验收：清理前复查源文件 `size`、`mtime_ns`、`volume_id`/`file_index`，任何不一致都禁止清理并记录失败原因。
- P2-12 已验收：默认清理方式为 Windows 回收站；支持用户指定隔离目录；永久删除作为高级选项，必须通过清理确认短语和永久删除确认短语。
- P2-12 已验收：每个清理结果写入 `source_cleanup_records` 并同事务写入 `sync_outbox`，同时更新来源引用的清理状态；输出和 UI 默认只展示路径 hash、文件名、状态和错误摘要。
- P2-12 已验收：客户端清理服务/UI 定向测试、全量 pytest、compileall、`git diff --check` 通过；Go 本阶段不改服务端代码，仅执行工具链自检并按已知沙箱问题记录结果。

## 开发排期

后续开发必须按下表推进。临时 fix 或新增需求只有在安全、数据一致性、真实联调阻塞或用户明确要求时可以插队；插队前必须在“排期变更记录”中写明原因、影响阶段、验收标准和回到主排期的条件。

| 优先级 | 阶段 | 工作内容 | 当前状态 | 下一步验收 |
| --- | --- | --- | --- | --- |
| P0 | 0-1 项目底座 | 仓库初始化、产品规格、Python 客户端骨架、配置加载、日志脱敏、uv 依赖 | 已完成 | 只做维护；入口 README 需保持与真实进度一致 |
| P0 | 2 本地/云端同步底座 | SQLite 迁移、版本字段、`sync_outbox`、Go Cloud Sync、PostgreSQL revision 投影、设备认证 | 基本完成 | 后续业务表接入时必须同事务写 outbox，并用真实云端 summary 校验 |
| P0 | 3 百度授权与上传底座 | OAuth/扫码授权、DPAPI Device Token、KDF store、token 解密、刷新租约、容量、预上传、分片、create、删除清理 | 基本完成 | 保持真实百度 API 验收；补上传失败重试 UI 和 token refresh 自动接入 |
| P0 | 4 远端对象校对与人工修复 | 百度 `list/listall`、本地 `remote_objects` 对账、差异状态、只读报告、人工修复入口 | 已完成；P0 阶段 4 远端对象校对与人工修复开发完成 | 下一个开发阶段为 P1 阶段 5 备份任务主 UI 与任务模型 |
| P1 | 5 备份任务主 UI 与任务模型 | 任务页、拖拽/选择源、暂停继续取消、状态机、任务持久化 | 已完成；P1 阶段 5 备份任务主 UI 与任务模型开发完成 | 下一个开发阶段为 P1 阶段 6 扫描与内容指纹 |
| P1 | 6 扫描与内容指纹 | 递归扫描、不可读记录、快速指纹、完整 MD5/SHA256、文件夹 manifest hash | 已完成；P1 阶段 6 扫描与内容指纹开发完成 | 下一个开发阶段为 P1 阶段 7 去重索引与来源引用 |
| P1 | 7 去重索引与来源引用 | 本地内容对象、归档对象、来源映射、云端去重候选查询 | 已完成；P1 阶段 7 去重索引与来源引用开发完成 | 下一个开发阶段为 P1 阶段 8 7-Zip 加密归档与 manifest |
| P1 | 8 7-Zip 加密归档与 manifest | 明文 manifest 临时生成、7-Zip AES-256、archive 分包、标准/严格验证、验证后删除明文 manifest | 已完成；P1 阶段 8 7-Zip 加密归档与 manifest 开发完成 | 下一个开发阶段为 P1 阶段 9 端到端备份编排 |
| P1 | 9 端到端备份编排 | 扫描 -> 指纹 -> 去重 -> manifest/archive -> 验证 -> 百度可恢复上传 -> outbox 同步 -> 远端校对 | 已完成；P1 阶段 9 端到端备份编排开发完成 | 下一个开发阶段为 P2 阶段 10 缓存额度与 artifact 管理 |
| P2 | 10 缓存额度与 artifact 管理 | 缓存目录、40GiB 规则、可释放统计、清理等级、artifact 生命周期 | 已完成；P2 阶段 10 缓存额度与 artifact 管理开发完成 | 下一个开发阶段为 P2 阶段 11 来源映射和校对 UI |
| P2 | 11 来源映射和校对 UI | 来源与远端映射页、数据库与百度校对页、差异筛选、人工确认修复 | 已完成；P2 阶段 11 来源映射和校对 UI 开发完成 | 下一个开发阶段为 P2 阶段 12 原始数据清理 |
| P2 | 12 原始数据清理 | 手动触发、回收站优先、清理前源文件复查、清理记录同步 | 已完成；P2 阶段 12 原始数据清理开发完成 | 下一个开发阶段为 P2 阶段 13 恢复流程 |
| P2 | 13 恢复流程 | 选择恢复对象、下载 archive、解密解压、按 manifest 恢复、SHA256 复验、冲突默认保留两者 | 未开始 | 原路径/手动路径恢复均可验收，覆盖缺失外部 archive 和密码错误 |
| P3 | 14 打包发布与最终验收 | PyInstaller/Nuitka、版本号、构建产物、发布文档、端到端验收矩阵 | 未开始 | 干净 Windows 环境完成安装、授权、备份、校对、清理、恢复和卸载/升级测试 |

## 进度差异审计

- README 入口文档曾停留在“Go 云端同步服务基础接口、部署构建脚本和百度授权管理接口”阶段，低估了实际进度；实际代码已具备真实百度授权、真实上传、断点续传、outbox 同步和清理联调能力。本轮同步更新 README 当前状态。
- README 曾继续把备份任务主 UI、扫描与内容指纹、去重索引、7-Zip AES-256 加密归档、加密 manifest 和缓存额度管理列为未完成；按 2026-06-08 近期提交和测试记录，这些阶段已经完成，本轮先修复入口文档口径。
- 原排期把“PySide6 基础 UI、任务页、设置页”合并为一个阶段，容易误判 UI 接近完成；实际到 P2-12 已完成百度设置页、备份任务页、来源映射/校对 UI 和原始数据清理页，仍缺恢复页和最终发布体验。
- 原排期把“百度 OAuth、预上传、分片上传、创建文件”标为部分完成；按近期提交和真实批测结果，该底座已基本完成，主备份编排也已接入扫描、7-Zip、manifest、真实上传、outbox 同步、远端校对和缓存 artifact 生命周期。
- 原排期未单独列出“进度审计、排期治理、临时 fix 记录规则”；本轮新增排期变更约束，后续任何插队需求都必须有文字说明。
- 产品规格要求的恢复尚未实现；P2-12 已补齐原始数据清理入口，但不能把清理能力等同于完整恢复和最终发布就绪。

## 排期变更记录

### 2026-06-08：P1-9 端到端备份编排真实百度全链路补测

变更原因：用户明确要求“补全真实百度上传测试，要求全链路实测，使用本机的已有密钥”；P1 阶段 9 原完成记录中仍标注真实 `backup_pipeline_cli --upload --sync-outbox --reconcile-remote` 需要人工联调窗口执行。

影响阶段：挂靠 P1 阶段 9 端到端备份编排，不改变主排期；完成后继续回到 P2 阶段 10 缓存额度与 artifact 管理。

验收标准：新增可复跑真实全链路测试入口；使用本机 DPAPI Device Token、KDF store 和运行时授权密码，真实执行扫描、去重、7-Zip 归档、百度可恢复上传、Cloud Sync、远端校对、completed final sync、同路径冲突探针和百度删除清理；输出保持脱敏。

回到主排期条件：真实全链路跑测成功、远端清理完成、客户端回归检查通过并提交后，下一阶段回到 P2 阶段 10。

### 2026-06-08：辅助 JSON 远端 md5 口径误报修复

变更原因：真实 keep-remote 校对联调发现 archive 本体一致，但 `.meta.json` 与 `job.index.json` 被报告为 `remote_meta_mismatch`；核对代码后确认本地账本对辅助 JSON 保存的是本地字节 MD5，而校对比较的是百度 `list/listall` 返回的远端对象 md5，属于口径不一致导致的误报。

影响阶段：仍属于 P0 远端对象校对与上传底座联调阻塞修复，不改变既定 P0/P1 顺序；修复后回到人工确认/修复入口设计。

验收标准：上传账本写入单元测试覆盖辅助 JSON 使用百度 create 返回 md5，同时保留本地 `sha256` 内容校验；客户端定向/全量测试、`compileall` 和 `git diff --check` 通过；真实 keep-remote 校对本批 3 个远端对象全部 `consistent`，随后用 `cleanup-resumable` 清理且删除 errno 为 0。

回到主排期条件：本次修复提交并完成真实清理后，继续 P0 远端对象校对与人工修复入口设计。

### 2026-06-07：进度审计与排期治理约束

变更原因：用户要求审计项目进度，确定离完整完成开发计划和发布还差多少，核对项目进度文件与实际进度差异，并要求后续开发遵循排期。

影响阶段：新增 P0 文档治理工作；重新拆分 P0-P3 后续阶段，把远端对象校对列为下一阶段，把主备份 UI、扫描、去重、7-Zip/manifest、缓存、清理、恢复和打包验收列为后续主线。

验收标准：`docs/roadmap_progress.md` 包含审计结论、差异说明和可执行排期；`AGENTS.MD` 包含后续必须遵循排期以及临时 fix/新增需求必须同步更新进度和排期的约束；README 当前状态不再停留在旧阶段。

回到主排期条件：本轮文档约束提交完成后，下一开发项回到 P0 远端对象校对 worker。

## 完成记录

### P2 阶段 12 原始数据清理

- P2 阶段 12 原始数据清理开发完成；下一个开发阶段为 P2 阶段 13 恢复流程。
- 新增 SQLite 迁移 `client/migrations/sqlite/008_source_cleanup_records.sql`，创建 `source_cleanup_records` 同步实体，并为 `file_items` 补充 `file_volume_serial` 和 `file_index`，用于清理前文件身份复查。
- 新增 `client/src/auto_backup_client/file_identity.py`，Windows 下通过 `GetFileInformationByHandle` 读取 volume serial 和 file index；非 Windows 测试环境保留 size/mtime 复查。
- 扫描阶段已把文件身份摘要写入 `file_items`；内容指纹仍只基于文件字节，不混入路径、时间、设备或文件身份字段。
- 新增 `client/src/auto_backup_client/source_cleanup.py`，实现清理候选、清理前 `size/mtime_ns/volume_id/file_index` 复查、默认回收站、隔离目录、高级永久删除和版本化清理记录。
- 清理候选门槛已落地：job 必须 completed，archive 标准验证通过，upload session 必须 `remote_created`，`.meta.json`/`job.index.json` 必须 uploaded，本地三类 `remote_objects` 必须 `remote_created`；云端同步待同步只作为 UI 警告，不阻塞本地记录完整后的清理。
- 默认回收站清理使用 Windows `SHFileOperationW` + `FOF_ALLOWUNDO`；隔离目录使用用户指定目录；永久删除必须同时填写 `CLEANUP_SOURCES` 和 `PERMANENT_DELETE_SOURCES`。
- 官方依据记录：已记录 Microsoft Learn 官方 URL `SHFileOperationW`、`GetFileInformationByHandle` 和 `BY_HANDLE_FILE_INFORMATION`；浏览/搜索工具未返回正文，本地 `curl.exe -L` 因沙箱网络限制无法连接 `learn.microsoft.com`。本轮实现依据来自上述官方 API 页面名与 Win32 API 语义、项目既有规格和自动化测试，后续联网环境可按 README 中 URL 复核。
- 每个实际清理结果会写入 `source_cleanup_records` 并同事务写入 `sync_outbox`，同步 payload 过滤完整原始路径和隔离目录路径；同时更新 `content_references.cleanup_status` 为 `cleaned` 或 `cleanup_failed`。
- PySide6 主窗口新增“原始数据清理”页面，支持按 job/关键字筛选候选、查看 sync pending 警告、预演、回收站/隔离目录/永久删除方式和确认短语执行。
- 更新根 README 和 `client/README.md`，记录项目已完成到 P2-12、剩余主线为 P2-13/P3-14，并说明原始数据清理的安全边界与脱敏规则。
- 已验证定向测试 `tests/test_source_cleanup.py tests/test_sqlite_store.py` 通过，11 个测试通过。
- 已验证 UI/编排定向测试 `tests/test_source_cleanup.py tests/test_source_mapping.py tests/test_backup_task_page.py tests/test_backup_pipeline.py` 通过，18 个测试通过。
- 已验证客户端全量 `uv run python -m pytest -p no:cacheprovider --basetemp <repo>/.cache/pytest-basetemp-p2-12` 通过，126 个测试通过。
- 已验证 `client/` 下 `uv run python -m compileall src tests` 通过。
- 已验证仓库根目录 `git diff --check` 通过。
- 已执行 `cloud-api/` 下 Go 工具链自检：`go version` 返回 `go1.25.10 windows/amd64`；沙箱内 `go list runtime` 仍返回 `package runtime is not in std (C:\Program Files\Go\src\runtime)`，按既有约束提升后 `go list runtime` 返回 `runtime`。本阶段未修改 Go 服务端代码、迁移或路由，不需要重新编译部署 Go 服务。

提交摘要：本次提交完成 P2 阶段 12 原始数据清理，新增文件身份复查、版本化 `source_cleanup_records`、默认回收站/隔离目录/高级永久删除执行路径，并把原始数据清理页接入 PySide6 主窗口；清理结果进入本地 SQLite 和 `sync_outbox`，同步 payload 不携带完整原始路径或隔离目录路径。

后续待办：

- 下一个开发阶段为 P2 阶段 13 恢复流程；下一轮开始前必须在当前工作项写明“本次开发阶段：P2 阶段 13 恢复流程”。
- P2 阶段 13 需要实现按来源/任务/日期/文件名选择恢复对象、下载或复用 archive、7-Zip 解密解压、按 manifest 恢复、SHA256 复验和冲突默认保留两者。
- 恢复阶段必须复用 `source_cleanup_records`、`content_references.restore_status`、`cache_artifacts` 和 `local_fs`，不得把已清理源文件误判为不可恢复。

### P2 阶段 11 来源映射和校对 UI

- P2 阶段 11 来源映射和校对 UI 开发完成；下一个开发阶段为 P2 阶段 12 原始数据清理。
- 修复根 README 当前状态口径，明确项目已完成到 P2 阶段 11，剩余主线为 P2-12/P2-13/P3-14，离 v1.3 可交付约剩 20%-25%。
- 新增 `client/src/auto_backup_client/source_mapping.py`，提供面向 UI 的只读来源映射聚合服务，将 `backup_jobs`、`backup_sources`、`file_items`、`content_references`、`archives` 和 `remote_objects` 关联成可展示行。
- PySide6 主窗口新增“来源映射”和“远端校对”页面；来源映射页支持按 job 和关键字筛选，表格默认展示文件名、hash 摘要、状态、content/archive 摘要和远端路径 hash。
- 远端校对页复用 `RemoteObjectReconciler` 和 `RemoteObjectRepairer`，支持按 job、upload session 或 remote dir 校对；默认 dry-run，仅在填写 `APPLY_REMOTE_REPAIR` 确认短语后写入现有可审计修复动作。
- 远端校对 UI 的写入动作只包括标记远端缺失和接受百度 `size/md5/fs_id` 元数据；`baidu_only`、`remote_unreadable`、重传、重建数据库和灾备恢复仍只展示人工建议，留待后续阶段。
- 更新 `client/README.md`，记录主窗口当前页面、来源映射服务、远端校对 UI 的能力边界和脱敏要求。
- 已验证新增定向测试 `tests/test_source_mapping.py tests/test_backup_task_page.py` 通过，6 个测试通过。
- 已验证客户端全量 `uv run python -m pytest -p no:cacheprovider --basetemp <repo>/.cache/pytest-basetemp-p2-11` 通过，121 个测试通过。
- 已验证 `client/` 下 `uv run python -m compileall src tests` 通过。
- 已验证仓库根目录 `git diff --check` 通过。
- 已执行 `cloud-api/` 下 Go 工具链自检：`go version` 返回 `go1.25.10 windows/amd64`；沙箱内 `go list runtime` 仍返回 `package runtime is not in std (C:\Program Files\Go\src\runtime)`，符合已沉淀的沙箱/标准库读取问题。本阶段未修改 Go 服务端代码、迁移或路由，不需要重新编译部署 Go 服务。

提交摘要：本次提交完成 P2 阶段 11 来源映射和校对 UI，修复入口 README 进度口径，新增来源映射只读聚合服务，并把来源映射页和远端校对页接入 PySide6 主窗口；远端修复继续复用既有版本化修复服务和确认短语门槛，避免 UI 绕过本地 SQLite + sync_outbox 的事务边界。

后续待办：

- 下一个开发阶段为 P2 阶段 12 原始数据清理；下一轮开始前必须在当前工作项写明“本次开发阶段：P2 阶段 12 原始数据清理”。
- P2 阶段 12 需要在清理前复查源文件 `size`、`mtime_ns` 和 Windows 文件身份信息，默认移入回收站，并把清理记录版本化同步。
- 后续恢复和灾备重建阶段如果需要从 `baidu_only` 或缺失远端对象恢复数据库，必须在新阶段单独实现，不得扩展 P2-11 的安全修复动作为自动重建或重传。

### P2 阶段 10 缓存额度与 artifact 管理

- P2 阶段 10 缓存额度与 artifact 管理开发完成；下一个开发阶段为 P2 阶段 11 来源映射和校对 UI。
- 新增 SQLite 迁移 `client/migrations/sqlite/007_cache_artifacts.sql`，创建本地-only `cache_artifacts` 表；缓存路径、staging、verify、上传临时 JSON 等本地状态不进入 `sync_outbox`。
- 新增 `client/src/auto_backup_client/cache_artifacts.py`，实现 artifact 登记、cache root 边界校验、占用统计、有效预算检查、缓存等级分类和 dry-run/实际清理。
- 新增 `client/src/auto_backup_client/cache_artifacts_cli.py`，提供 `status` 和 `cleanup` 入口；输出只包含缓存等级、大小、数量和路径 SHA256，不输出真实缓存路径。
- `ArchivePackager` 已登记 `manifest_plain`、staging、verify 和最终 archive；临时目录标准验证后标记为 deleted，最终 archive 保留为 active artifact。
- `BaiduResumableUploader` 已将 `.meta.json` 和 `job.index.json` 上传临时文件放入 cache root 下的 `upload_tmp/` 并登记为 `upload_temp` artifact，上传结束后标记 deleted。
- `BackupPipeline` 支持 40GiB 有效缓存预算检查、缓存等级清理 dry-run/实际清理、archive 远端确认后标记 `remote_confirmed`；预算不足时在扫描/哈希/压缩前拒绝新任务且不改变 queued job 状态。
- `backup_pipeline_cli` 默认启用缓存预算检查，支持 `--skip-cache-budget-check`、预算参数和 `--cleanup-cache-artifacts`；真实全链路测试入口也输出缓存预算和清理 dry-run 摘要。
- 修复 `real_backup_pipeline_test_cli` 可复跑冲突问题：临时源文件按 run id 生成确定性内容，避免多次真实验收复用相同 `content_id` 后与云端历史 content revision 冲突。
- 更新 `client/README.md`，记录缓存 artifact 服务、CLI、40GiB 预算门槛、安全清理边界和输出脱敏要求。
- 已验证客户端定向测试 `tests/test_cache_artifacts.py tests/test_backup_pipeline.py tests/test_archive_packager.py tests/test_baidu_resumable_upload.py tests/test_sqlite_store.py` 通过，26 个测试通过。
- 已验证客户端全量 `uv run python -m pytest -p no:cacheprovider --basetemp <repo>/.cache/pytest-basetemp-all-p2-cache` 通过，117 个测试通过。
- 已验证 `client/` 下 `uv run python -m compileall src tests` 通过。
- 已验证真实 `real_backup_pipeline_test_cli --password-env BAIDU_AUTH_PASSWORD` 通过：`completed=true`、`cache_level_before=sufficient`、`cache_effective_budget_bytes_before=42949672960`、`cache_cleanup_dry_run=true`、`uploaded_part_count=2`、`sync_synced=25`、无 conflict/rejected/retryable、远端 3 个对象全部 `consistent`、`completed_job_cloud_summary_verified=true`、同路径冲突探针错误码 `-8`、本批远端清理 `cleanup_delete_errno=0`。
- 已补清理前一次固定源内容导致 `content_objects` 云端 revision conflict 的失败真实验收残留远端对象，`cleanup_object_count=3` 且 `cleanup_delete_errno=0`。

提交摘要：本次提交完成 P2 阶段 10 缓存额度与 artifact 管理，新增本地-only artifact 记录、预算检查、缓存等级和安全清理入口，并把归档、上传临时文件和端到端 pipeline 接入统一 artifact 生命周期；清理仅作用于 cache root 内已登记 artifact，不触碰用户源文件或断点续传 SQLite 状态。

后续待办：

- 下一个开发阶段为 P2 阶段 11 来源映射和校对 UI；下一轮开始前必须在当前工作项写明“本次开发阶段：P2 阶段 11 来源映射和校对 UI”。
- P2 阶段 11 需要在 UI 中展示来源、归档、远端对象和百度实际状态的映射关系，并保留所有人工修复动作的版本记录。
- 后续恢复、下载缓存和严格验证接入时，必须复用 `cache_artifacts` 和 `local_fs`，不得新增未登记的可删除缓存目录。

### P1 阶段 9 长路径公共 API 硬化

- P1 阶段 9 长路径公共 API 硬化完成；下一个开发阶段为 P2 阶段 10 缓存额度与 artifact 管理。
- 新增 `client/src/auto_backup_client/local_fs.py`，集中封装 Windows `\\?\` 长路径包装以及 open/stat/exists/is_file/mkdir/unlink/rmtree/replace 等本地文件操作。
- `archive_packager.py`、`baidu/upload.py` 和 `baidu/resumable_upload.py` 已改为复用公共本地文件工具，移除重复 `_fs_path` 实现。
- 归档流程的 manifest 写入、staging payload 复制、临时目录创建/删除、archive rename/stat/hash，以及百度上传分片读取和 mtime 读取均通过同一长路径安全层。
- 新增 `client/tests/test_local_fs.py`，覆盖超过 260 字符路径下的 open/stat/exists/is_file/replace/rmtree/mtime 行为，并确认业务路径字符串不包含 `\\?\` 前缀。
- 已验证客户端定向测试 `tests/test_local_fs.py tests/test_archive_packager.py tests/test_baidu_upload.py tests/test_baidu_resumable_upload.py` 通过，22 个测试通过。

提交摘要：本次提交把 P1-9 真实全链路暴露出的 Windows 长路径访问逻辑收口为公共 `local_fs` 工具层，归档和百度上传链路不再各自维护 `_fs_path`，为 P2 阶段 10 的缓存 artifact 生命周期与清理逻辑提供统一文件访问基础。

后续待办：

- 下一个开发阶段为 P2 阶段 10 缓存额度与 artifact 管理；下一轮开始前必须在当前工作项写明“本次开发阶段：P2 阶段 10 缓存额度与 artifact 管理”。
- P2 阶段 10 新增的 artifact 统计、清理和 pipeline 预算检查必须复用 `local_fs`，不得重新引入模块内 `_fs_path`。

### P1 阶段 9 端到端备份编排

- P1 阶段 9 端到端备份编排开发完成；下一个开发阶段为 P2 阶段 10 缓存额度与 artifact 管理。
- 新增 `client/src/auto_backup_client/backup_pipeline.py`，将 `BackupScanner`、`ContentDedupeIndexer`、`ArchivePackager`、`BaiduResumableUploader`、`SyncOutboxWorker` 和 `RemoteObjectReconciler` 串成单 job 最小主流程。
- 新增 `client/src/auto_backup_client/backup_pipeline_cli.py`，默认只执行本地扫描、去重和 7-Zip 归档；显式传 `--upload --sync-outbox --reconcile-remote` 后才读取 Device Token、解密百度 token 并接入真实百度/云端链路。
- 归档上传 ID 已对齐：`ResumableArchiveInput` 支持显式 `archive_id`，P1-9 编排上传时沿用 P1-8 生成的 `archives.archive_id`，避免归档实体和上传账本出现两个 ID 口径。
- 扩展 `SQLiteClientStore.update_archive_remote_path(...)`，上传成功后把远端 `.7z` 路径回填到 `archives.remote_path` 并同事务进入 `sync_outbox`。
- 完成状态门槛已落地：本地闭环不标记 completed；上传模式下 `mark_completed=True` 必须同时启用 outbox 同步和远端校对，且校对无差异、同步无 conflict/rejected/retryable 后才把 job 更新为 `completed`。
- 失败边界已覆盖：上传阶段失败会把 running job 转为 `failed_retryable`，保留本地 archive 和 outbox，中间账本不丢失，不写 `remote_objects`，不误标 completed。
- 修复 P1-9 CLI 测试暴露的 Windows 长路径问题：归档服务对最终 `.7z` 的哈希读取、rename 和 stat 使用 Windows `\\?\` 长路径包装；百度上传分片计划、分片读取和 mtime 读取也支持长 archive 路径。
- 更新 `client/README.md`，记录 `BackupPipeline`、端到端编排 CLI、本地模式和真实模式示例、输出脱敏边界，以及本阶段不做缓存/恢复/清理/UI 编排的范围。
- 将 P1-9 归档长路径与 pytest 临时目录约束沉淀到 `AGENTS.MD`。
- 新增 `client/tests/test_backup_pipeline.py` 和 `client/tests/test_backup_pipeline_cli.py`，覆盖本地闭环、上传/同步/远端校对完成、失败不完成、完成前必须校对和 CLI 输出脱敏。
- 已验证客户端定向测试 `tests/test_backup_pipeline.py tests/test_backup_pipeline_cli.py tests/test_archive_packager.py tests/test_dedupe_index.py tests/test_scan_fingerprints.py tests/test_baidu_resumable_upload.py tests/test_baidu_reconcile.py tests/test_sync_worker.py tests/test_sqlite_store.py` 通过，46 个测试通过。
- 已验证客户端全量 `uv run python -m pytest -p no:cacheprovider --basetemp <repo>/.cache/pytest-basetemp-pipeline-all3` 通过，107 个测试通过。
- 已验证 `client/` 下 `uv run python -m compileall src tests` 通过。
- 已验证仓库根目录 `git diff --check` 通过。
- 已验证沙箱内 Go 自检 `go version; go list runtime` 仍出现 `package runtime is not in std`，按既有约束提升权限后 `go version; go list runtime; go test ./...` 通过，确认不是 Go 服务代码回归。
- P1 阶段 9 真实百度全链路补测已完成；下一个开发阶段仍为 P2 阶段 10 缓存额度与 artifact 管理。
- 新增 `client/src/auto_backup_client/real_backup_pipeline_test_cli.py`，作为可复跑真实主流程验收入口：默认使用仓库 `.cache/real-pipeline/` 临时 SQLite、缓存和源文件目录，复用本机 DPAPI Device Token 和 KDF store，从运行时环境读取授权/归档密码。
- 真实测试入口会生成小文件和跨 4 MiB 分片源文件，执行扫描、去重、7-Zip 归档、百度容量检查、可恢复上传、Cloud Sync、远端校对、job completed、completed final sync、同路径 `rtype=0` 冲突探针和百度 `filemanager/delete` 清理。
- `BackupPipeline` 已补齐 completed final sync：远端校对一致并写入 job `completed` 后，会再次同步 outbox，把最终完成状态 revision 推送到真实云端。
- 修复真实全链路暴露的 Windows 长路径问题：payload staging 复制、staging 目录删除、`BaiduResumableUploader` archive SHA256 和 mtime 读取均改为 Windows `\\?\` 长路径安全包装。
- 更新 `client/README.md`，将 `real_backup_pipeline_test_cli` 记录为 P1-9 主流程真实验收入口，并把 `integration_cli run-resumable` 定位为底层上传账本排障入口。
- 将 staging payload、staging cleanup 和 resumable archive 读取长路径问题沉淀到 `AGENTS.MD`。
- 新增 `client/tests/test_real_backup_pipeline_test_cli.py`，覆盖真实测试入口的主控制流、completed summary 校验、冲突探针、远端清理和输出脱敏。
- 已使用本机已有 DPAPI Device Token、KDF store 和 `client/.env` 中运行时授权密码执行真实 `uv run python -m auto_backup_client.real_backup_pipeline_test_cli --password-env BAIDU_AUTH_PASSWORD`：`completed=true`、`uploaded_part_count=2`、`sync_selected=25`、`sync_synced=25`、`sync_conflicts=0`、`sync_rejected=0`、`sync_retryable=0`、`reconcile_consistent=3`、`completed_job_cloud_summary_verified=true`、`conflict_probe_error_code=-8`、`cleanup_object_count=3`、`cleanup_delete_errno=0`。
- 已验证客户端定向测试 `tests/test_backup_pipeline.py tests/test_real_backup_pipeline_test_cli.py tests/test_backup_pipeline_cli.py` 通过，9 个测试通过。

提交摘要：本次提交完成 P1 阶段 9 真实百度全链路补测，新增 `real_backup_pipeline_test_cli` 作为可复跑主流程验收入口，补齐 completed final sync，并修复 staging payload、staging cleanup 和 resumable archive 读取长路径问题；已用本机已有凭据跑通真实百度上传、同步、远端校对、同路径冲突探针和远端清理。

后续待办：

- 下一个开发阶段为 P2 阶段 10 缓存额度与 artifact 管理；下一轮开始前必须在当前工作项写明“本次开发阶段：P2 阶段 10 缓存额度与 artifact 管理”。
- P2 阶段 10 需要新增缓存 artifact 记录、40GiB 有效预算、可释放统计、缓存清理等级和 artifact 生命周期；不得删除用户源文件。
- P2 阶段 10 需要把 P1-8/P1-9 的 archive、manifest_plain、staging、verify、upload 临时文件纳入统一 artifact 生命周期，并明确哪些阶段前不可删除。
- 后续 P2/P3 每次改动归档、缓存、恢复或校对链路后，应优先复跑 `real_backup_pipeline_test_cli`，并确认远端清理 `cleanup_delete_errno=0`。

### P1 阶段 8 7-Zip 加密归档与 manifest

- P1 阶段 8 7-Zip 加密归档与 manifest 开发完成；下一个开发阶段为 P1 阶段 9 端到端备份编排。
- 按用户要求安装真实 7-Zip 后再做压缩验收：从 7-Zip 官方首页指向的 GitHub release 下载 `7z2601-x64.exe`，SHA256 为 `D64A0468F5B5B0B0FC5B2188450BCD655B70809D97B1C4535F2884635094377D`；安装后 `C:\Program Files\7-Zip\7z.exe` 显示 7-Zip 26.01。
- 官方依据记录：已通过 `curl.exe -L https://www.7-zip.org/` 获取 7-Zip 官方首页，确认 7z/ZIP 支持 AES-256 加密和存在命令行版本；浏览/搜索工具与 `curl.exe` 均未获取到 7-Zip CHM 命令行参数页内容，直链返回 404。本轮 CLI 参数依据来自项目规格、官方首页能力说明和本机真实 7-Zip 26.01 行为验收。
- 新增 SQLite 迁移 `client/migrations/sqlite/006_archive_manifest.sql`，包含版本化同步实体 `archives` 和本地归档成员索引 `archive_members`。
- 扩展 `SQLiteClientStore`，新增 `put_archive`、`put_archive_member`、`list_archives`、`list_archive_members` 和 `update_content_reference_archive`；`archives` 会同事务写入 `sync_outbox`，本地 `local_archive_path` 不进入同步 payload 和规范化记录哈希。
- 新增 `client/src/auto_backup_client/archive_packager.py`，实现本地归档服务：读取 `backup_jobs`、`backup_sources`、`content_references`、`file_items`、`folder_items`，生成稳定 manifest，把 `needs_payload` 内容 staging 到 `payload/{content_id}`，并调用真实 7-Zip 生成加密 7z archive。
- 标准验证已落地：打包后计算 archive MD5/SHA256，执行 `7z t`，再解出 `manifest/manifest.json` 并校验 manifest SHA256；验证通过后写入 `archives`、`archive_members` 并回填 `content_references.archive_id/archive_sha256/archive_member_path`。
- 明文 manifest 生命周期已覆盖：`manifest_plain/`、压缩 staging 目录和 verify 解压目录会在标准验证结束后删除；测试确认 outbox 不包含用户密码、本地 archive 路径、明文 manifest 路径或 staging 路径。
- 支持 manifest-only archive：当某个 job 全部来源都是本地重复或云端候选引用时，仍会生成只包含 manifest 的 archive，用于保存本次 job 的恢复语义。
- 更新 `client/README.md`，记录 `ArchivePackager`、7-Zip 可执行文件发现顺序、真实测试密码、明文 manifest 生命周期和本阶段不接入上传/缓存/恢复/UI 编排的边界。
- 将本机缺少 7-Zip 且不得模拟压缩验收的问题沉淀到 `AGENTS.MD`。
- 新增 `client/tests/test_archive_packager.py`，使用真实 7-Zip 和密码 `Test123456789` 覆盖混合 payload/archive、manifest-only archive、源文件扫描后变化拒绝、`7z t` 标准验证、manifest 解压 SHA256 复核和明文目录清理。
- 扩展 `client/tests/test_sqlite_store.py`，覆盖新增 `archives` 和 `archive_members` 迁移。
- 已验证客户端定向测试 `tests/test_archive_packager.py tests/test_sqlite_store.py` 通过，9 个测试通过。
- 已验证客户端全量 `uv run python -m pytest -p no:cacheprovider --basetemp <repo>/.cache/pytest-basetemp-archive-all` 通过，100 个测试通过。
- 已验证 `client/` 下 `uv run python -m compileall src tests` 通过。
- 已验证仓库根目录 `git diff --check` 通过。

提交摘要：本次提交完成 P1 阶段 8 7-Zip 加密归档与 manifest，新增本地归档/manifest 表和 `ArchivePackager` 服务，把去重引用接入真实 7-Zip 加密归档、标准验证、manifest SHA256 复核和明文临时目录清理；`archives` 已作为同步实体进入 `sync_outbox`，但本阶段不执行百度上传、远端元数据生成、缓存额度调度、严格验证、恢复或 UI 编排。

后续待办：

- 下一个开发阶段为 P1 阶段 9 端到端备份编排；下一轮开始前必须在当前工作项写明“本次开发阶段：P1 阶段 9 端到端备份编排”。
- P1 阶段 9 需要把任务创建、扫描、去重、`ArchivePackager`、百度 `upload-resumable`、`sync-outbox` 和远端校对串成最小端到端主流程，并继续使用真实云端 API 和真实百度 API 验收。
- P1 阶段 9 接入上传后，需要生成并上传 `.meta.json`/`job.index.json`，把 archive 远端路径、fs_id、远端 md5 和校对状态回写到现有上传/远端对象账本。
- 严格解压验证、缓存额度/生命周期、来源映射 UI、原始数据清理和恢复仍留到 P2 对应阶段，不能把本阶段标准验证等同于完整发布就绪。

### P1 阶段 7 去重索引与来源引用

- P1 阶段 7 去重索引与来源引用开发完成；下一个开发阶段为 P1 阶段 8 7-Zip 加密归档与 manifest。
- 新增 SQLite 迁移 `client/migrations/sqlite/005_dedupe_content_index.sql`，包含版本化同步实体 `content_objects` 和本地来源引用表 `content_references`。
- 扩展 `SQLiteClientStore`，新增 `put_content_object`、`put_content_reference`、`replace_content_references_for_job`、`get_content_object_for_update`、`list_content_objects` 和 `list_content_references`；`content_objects` 会同事务写入 `sync_outbox`，`content_references.local_path` 只保存在本地 SQLite。
- 新增 `client/src/auto_backup_client/dedupe_index.py`，实现内容级去重索引服务：读取 `file_items`，只处理 `full_hashed` 稳定扫描结果，校验 `content_id` 必须由完整 SHA256+size 推导，拒绝内容 ID 与 SHA256/size 不一致的数据。
- 本地重复引用策略已落地：同一内容的首个本地来源标记为 `payload_source/needs_payload`，后续来源标记为 `local_duplicate`；跨 job 已有 payload 来源时，新 job 的相同内容引用直接标记为本地重复。
- 重扫和级联删除计数校准已覆盖：当文件内容变化或旧 `file_items` 被替换时，旧 `content_objects.reference_count`、`payload_reference_count` 和 `duplicate_reference_count` 会按当前引用重新校准。
- 扩展 `BaiduCloudClient.get_content(...)` 和 `ContentObject` 模型，封装云端 `GET /v1/contents/{content_id}` 候选查询；只有云端返回的 `file_sha256` 与 `size_bytes` 同本地一致时，才把引用标记为 `cloud_duplicate_candidate`。
- 更新 `client/README.md`，记录内容级去重表、最终去重口径、云端候选判断边界和本阶段不生成 7-Zip/manifest/archive 的范围。
- 新增 `client/tests/test_dedupe_index.py`，覆盖同内容多来源只创建一条内容对象、跨 job 本地重复、云端候选 sha256+size 校验、hash mismatch 不跳过 payload、重扫引用计数回落和异常 `content_id` 拒绝。
- 扩展 `client/tests/test_baidu_cloud_api.py` 和 `client/tests/test_sqlite_store.py`，覆盖云端内容候选解析和新增 SQLite 表迁移。
- 已验证客户端定向测试 `tests/test_dedupe_index.py tests/test_sqlite_store.py tests/test_baidu_cloud_api.py` 通过，19 个测试通过。
- 已验证客户端全量 `uv run python -m pytest -p no:cacheprovider --basetemp <repo>/.cache/pytest-basetemp-dedupe-all` 通过，97 个测试通过。
- 已验证 `client/` 下 `uv run python -m compileall src tests` 通过。
- 已验证仓库根目录 `git diff --check` 通过。

提交摘要：本次提交完成 P1 阶段 7 去重索引与来源引用，新增本地内容对象和来源引用模型，把扫描结果接入最终去重口径；`content_objects` 可同步到云端索引，`content_references` 保留本地来源路径但不进入同步 payload。本阶段不生成 archive、manifest 或执行百度上传。

后续待办：

- 下一个开发阶段为 P1 阶段 8 7-Zip 加密归档与 manifest；下一轮开始前必须在当前工作项写明“本次开发阶段：P1 阶段 8 7-Zip 加密归档与 manifest”。
- P1 阶段 8 需要把 `content_references` 中 `needs_payload` 的来源打包进 7-Zip AES-256 archive，并生成加密 manifest；明文 manifest 只允许短暂存在于缓存临时目录，验证通过后必须删除。
- P1 阶段 8/9 接入 archive 后，需要回填 `content_references.archive_id`、`archive_sha256` 和 `archive_member_path`，并新增/复用 `archive_objects` 归档索引，继续保持最终去重只使用完整 SHA256+size。

### P1 阶段 6 扫描与内容指纹

- P1 阶段 6 扫描与内容指纹开发完成；下一个开发阶段为 P1 阶段 7 去重索引与来源引用。
- 新增 SQLite 迁移 `client/migrations/sqlite/004_scan_fingerprints.sql`，包含版本化同步实体 `file_items`、`folder_items` 和本地扫描问题表 `scan_issues`。
- 扩展 `SQLiteClientStore`，新增扫描结果替换、`put_file_item`、`put_folder_item`、`put_scan_issue` 和列表读取方法；`file_items`/`folder_items` 会同事务写入 `sync_outbox`，`local_path` 作为本地字段被过滤，不进入同步 payload 和规范化记录哈希。
- 新增 `client/src/auto_backup_client/scan_fingerprints.py`，实现文件和目录来源扫描、递归普通目录、默认跳过 symlink/junction/`.lnk` 快捷方式、不可读文件/目录问题记录、动态采样 quick fingerprint、完整 MD5/SHA256、`content_id`、文件夹 `folder_content_hash` 和 `folder_manifest_hash`。
- 扫描结果稳定 item id 基于 `backup_source_id + relative_path`，同一 job/source 重新扫描时覆盖当前 `file_items`/`folder_items` 行并递增稳定 item 的 `data_version`，保留已入队的历史 revision，方便后续云端按 revision 审计。
- 内容指纹继续遵循产品安全约束：quick fingerprint、完整 MD5/SHA256 和 `content_id` 只由文件字节大小与内容哈希决定，不混入路径、名称、时间、设备或任务信息；路径和时间仅用于后续 manifest/恢复语义。
- 更新 `client/README.md`，记录扫描服务范围、SQLite 表、链接/快捷方式跳过规则、不可读问题处理、内容指纹边界和后续 P1-7 到 P1-9 接入方向。
- 新增 `client/tests/test_scan_fingerprints.py`，覆盖路径无关指纹、动态采样边界、folder content/manifest hash 口径、扫描持久化与 outbox 脱敏、不可读文件不中断、重扫版本递增和链接/快捷方式分类。
- 已验证客户端定向测试 `tests/test_scan_fingerprints.py tests/test_sqlite_store.py` 通过，13 个测试通过。
- 已验证客户端全量 `uv run python -m pytest -p no:cacheprovider --basetemp <repo>/.cache/pytest-basetemp-scan-fingerprint` 通过，90 个测试通过。
- 已验证 `client/` 下 `uv run python -m compileall src tests` 通过。
- 已验证仓库根目录 `git diff --check` 通过。

提交摘要：本次提交完成 P1 阶段 6 扫描与内容指纹，新增本地扫描表和扫描服务，把备份任务来源转化为可同步的文件/文件夹指纹记录；扫描默认跳过链接和快捷方式，遇到不可读问题不中断，且同步 payload 不包含本地绝对路径。本阶段不执行去重索引、来源引用、归档、上传、清理或恢复。

后续待办：

- 下一个开发阶段为 P1 阶段 7 去重索引与来源引用；下一轮开始前必须在当前工作项写明“本次开发阶段：P1 阶段 7 去重索引与来源引用”。
- P1 阶段 7 需要新增本地 `content_objects`、`content_references` 和必要的来源引用模型，把本轮 `file_items.content_id`、`sha256` 和 `size_bytes` 接入最终去重判断。
- P1 阶段 7 接入云端去重候选查询时，最终重复判断仍只能使用完整 SHA256+size；路径、时间、设备和任务信息不得进入内容指纹。

### P1 阶段 5 备份任务主 UI 与任务模型

- P1 阶段 5 备份任务主 UI 与任务模型开发完成；下一个开发阶段为 P1 阶段 6 扫描与内容指纹。
- 新增 SQLite 迁移 `client/migrations/sqlite/003_backup_jobs.sql`，包含版本化同步实体 `backup_jobs` 和本地来源清单 `backup_sources`；`backup_jobs` 写入 `sync_outbox`，`backup_sources.local_path` 当前只保存在本地 SQLite。
- 扩展 `SQLiteClientStore`，新增 `put_backup_job`、`put_backup_source`、`get_backup_job`、`list_backup_jobs` 和 `list_backup_sources`，并让同步状态回写支持 `backup_jobs`。
- 新增 `client/src/auto_backup_client/backup_jobs.py`，提供任务创建、来源规范化、重复来源拒绝、任务状态标签和受控状态机；当前允许 `queued -> running -> paused -> running`、取消路径和可重试失败后继续。
- 新增 `client/src/auto_backup_client/ui/main_window.py`，提供 PySide6 主窗口、左侧导航、备份任务页和百度设置页入口；备份任务页支持选择文件、选择文件夹、拖拽添加、移除/清空待建来源、创建任务、开始、暂停、继续和取消。
- 本轮 PySide6 UI 结构参考 Qt for Python 官方文档：`QMainWindow`（`https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QMainWindow.html`）、`QStackedWidget`（`https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QStackedWidget.html`）和 `QFileDialog`（`https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QFileDialog.html`）。
- UI 状态栏和任务列表不显示完整本地路径；待建来源表只展示类型、名称和路径 SHA256 前缀，避免把源路径扩散到普通状态文案。
- 更新 `client/README.md`，记录主窗口运行方式、阶段边界和 `backup_sources.local_path` 不进入 `sync_outbox` 的约束。
- 新增 `client/tests/test_backup_jobs.py`，覆盖任务创建持久化、空来源/重复来源拒绝、状态机版本化更新、非法跳转不写新 revision 和 outbox 不包含本地来源路径。
- 新增 `client/tests/test_backup_task_page.py`，覆盖 PySide6 任务页直接添加来源后可创建持久化任务，且创建/状态变更消息不泄露完整本地路径，开始/暂停/继续/取消能驱动模型状态变更。
- 已验证客户端定向测试 `tests/test_backup_jobs.py tests/test_backup_task_page.py` 通过，6 个测试通过。
- 已验证客户端全量 `uv run python -m pytest -p no:cacheprovider --basetemp <repo>/.cache/pytest-basetemp-task-ui` 通过，83 个测试通过。
- 已验证 `client/` 下 `uv run python -m compileall src tests` 通过。
- 已验证仓库根目录 `git diff --check` 通过。

提交摘要：本次提交完成 P1 阶段 5 备份任务主 UI 与任务模型，新增版本化 `backup_jobs`、本地 `backup_sources`、任务状态机服务层和 PySide6 主窗口/任务页；任务创建和状态变更已能持久化并进入 `sync_outbox`，但本轮不执行扫描、指纹、归档、上传、清理或恢复。

后续待办：

- 下一个开发阶段为 P1 阶段 6 扫描与内容指纹；下一轮开始前必须在当前工作项写明“本次开发阶段：P1 阶段 6 扫描与内容指纹”。
- P1 阶段 6 需要实现递归扫描、不可读记录、symlink/junction 默认跳过、快速指纹和完整 MD5/SHA256，并把扫描状态接回本轮 `backup_jobs`/`backup_sources` 模型。
- 后续接入 P1 阶段 7-9 前，仍需保持内容指纹不混入路径、时间、设备等附属信息，并继续避免把原始路径明文同步到云端。

### P0 阶段 4 远端对象校对与人工修复入口

- P0 阶段 4 远端对象校对与人工修复开发完成；下一个开发阶段为 P1 阶段 5 备份任务主 UI 与任务模型。
- 在 `AGENTS.MD` 新增“开发阶段接力规则”：每次开发开始必须写明本次开发阶段，完成后必须写明当前阶段开发完成和下一个开发阶段；临时 fix 也必须写明挂靠阶段、是否改变主排期和回到哪个阶段。
- 新增 `client/src/auto_backup_client/baidu/reconcile_repair.py`，把只读 `RemoteReconcileReport` 转换为人工修复候选动作；`consistent` 不允许修复，远端缺失类差异可标记 `remote_missing`，size/md5/fs_id 差异可选择接受百度 `list/listall` 元数据，`baidu_only` 与 `remote_unreadable` 只输出人工处理建议。
- 扩展 `client/src/auto_backup_client/baidu/reconcile_cli.py`，新增 `repair-remote-objects` 子命令。默认 dry-run，只输出脱敏候选动作；只有同时传入 `--apply --confirm APPLY_REMOTE_REPAIR` 时才写入。
- 扩展 `SQLiteClientStore.repair_remote_object(...)`，只允许更新 `remote_objects` 的 `status`、`size_bytes`、`md5`、`fs_id`，并复用 `build_version_fields -> put_remote_object -> sync_outbox`，保证业务表和 outbox 同一事务写入。
- 更新 `client/README.md`，记录人工修复 CLI 的 dry-run、显式确认、可写动作边界和不自动删除/覆盖/重传约束。
- 新增 `client/tests/test_baidu_reconcile_repair.py`，覆盖全部核心候选动作映射、dry-run 不写库、不写额外 outbox，以及 confirmed 模式写版本字段并入队 outbox。
- 扩展 `client/tests/test_baidu_reconcile_cli.py`，覆盖 `repair-remote-objects` 默认 dry-run、`--apply` 缺少确认时报错脱敏、显式确认后写入版本和 outbox，且输出不泄露 Device Token、百度 token、用户密码、SQLite 路径或远端真实路径。
- 已验证客户端定向测试 `tests/test_baidu_reconcile_repair.py tests/test_baidu_reconcile_cli.py` 通过，10 个测试通过。
- 已验证客户端全量 `uv run python -m pytest -p no:cacheprovider --basetemp <repo>/.cache/pytest-basetemp-reconcile-repair` 通过，77 个测试通过。
- 已验证 `client/` 下 `uv run python -m compileall src tests` 通过。
- 已验证仓库根目录 `git diff --check` 通过。

提交摘要：本次提交完成 P0 阶段 4 远端对象校对与人工修复入口，新增默认 dry-run 的 `repair-remote-objects` CLI 和受控 `remote_objects` 版本化修复写入路径；只有显式确认后才会把可证明的本地账本修复写入 SQLite 并进入 `sync_outbox`，需要删除、覆盖、重传、下载读取内容或完整 UI 的情况继续留到人工处理或后续 P2 校对 UI。

后续待办：

- 下一个开发阶段为 P1 阶段 5 备份任务主 UI 与任务模型；下一轮开始前必须在当前工作项写明“本次开发阶段：P1 阶段 5 备份任务主 UI 与任务模型”。
- P2 阶段 11 再实现完整来源映射和校对 UI，把本轮 CLI 候选动作接入可视化人工确认流程。
- 若后续需要对 `baidu_only` 对象进行导入或对不一致对象执行重传/下载/删除，必须另行实现受控动作，并继续保持显式确认、版本记录和 `sync_outbox` 同步。

### 辅助 JSON 远端 md5 口径修复与真实校对复验

- 已执行真实 `integration_cli run-resumable --keep-remote` 初次校对联调，确认上传 archive、`.meta.json`、`job.index.json` 成功，Cloud Sync 推送 `sync_selected=10`、`sync_sent=10`、`sync_synced=10`、`sync_conflicts=0`、`sync_rejected=0`、`sync_retryable=0`，云端摘要校验 `cloud_summary_verified=10`，并在校对后用 `cleanup-resumable` 清理 3 个远端对象且 `cleanup_delete_errno=0`。
- 初次校对发现 archive 本体 `consistent`，但 `.meta.json` 与 `job.index.json` 因 `remote_objects.md5` 记录口径与百度 `list/listall` 返回口径不一致被误判为 `remote_meta_mismatch`；两个辅助 JSON 对象 size 和 fs_id 均一致。
- 修复 `BaiduResumableUploader._write_remote_objects(...)`，对 archive、`.meta.json`、`job.index.json` 三类对象统一把 `remote_objects.md5` 记录为百度 create 返回的远端 md5；若百度未返回 md5，辅助 JSON 才回退本地字节 MD5。本地内容完整性继续保留在 `sha256` 字段，不新增 SQLite schema。
- 新增 `test_resumable_upload_records_baidu_md5_for_metadata_objects`，覆盖辅助 JSON 账本记录百度远端 md5，同时保留本地稳定 JSON SHA256。
- 已验证客户端定向测试 `tests/test_baidu_resumable_upload.py tests/test_baidu_reconcile.py` 通过，11 个测试通过。
- 已验证客户端全量 `uv run python -m pytest -p no:cacheprovider --basetemp <repo>/.cache/pytest-basetemp-all-md5` 通过，70 个测试通过。
- 已验证 `client/` 下 `uv run python -m compileall src tests` 通过。
- 已验证仓库根目录 `git diff --check` 通过。
- 已重新执行真实 `integration_cli run-resumable --keep-remote -> reconcile_cli remote-objects -> cleanup-resumable`：本批 `local_object_count=3`、`remote_object_count=3`、`status_consistent=3`、`status_remote_meta_mismatch=0`，Cloud Sync 仍为 `sync_synced=10` 且无 conflict/rejected/retryable，清理 3 个远端对象 `cleanup_delete_errno=0`。

提交摘要：本次提交修复辅助 JSON 远端 md5 记录口径，使 `.meta.json` 和 `job.index.json` 的本地 `remote_objects.md5` 与百度 `list/listall` 可比较字段一致，消除真实 keep-remote 校对中的误报；真实上传、同步、只读校对和远端清理链路已复验通过。

后续待办：

- 回到 P0 远端对象校对与人工修复入口设计，把只读报告中的建议动作映射到用户可确认的修复流程。
- 后续若需要校对辅助 JSON 内容语义，应新增受控下载/读取或在 `.meta.json` 中保存可比较内容哈希，不得继续混用百度远端 md5 和本地字节 MD5。
- 继续保持真实联调输出脱敏，并在每次 keep-remote 联调后立即执行 `cleanup-resumable`。

### 远端对象校对 worker 与脱敏 CLI

- 新增 `client/src/auto_backup_client/baidu/reconcile.py`，提供 `RemoteReconcileScope`、`RemoteReconcileFinding`、`RemoteReconcileReport`、`RequestRateLimiter` 和 `RemoteObjectReconciler`，按 `job_id`、`upload_session_id` 或 `remote_dir` 读取本地对象并调用百度 `list/listall` 校对。
- 扩展 `SQLiteClientStore` 只读查询，支持按 `job_id`、`upload_session_id` 或远端目录读取 `remote_objects` 与 `upload_sessions`；不新增 SQLite schema，不写入 `sync_outbox`，不改变同步状态。
- 差异状态覆盖 `consistent`、`db_exists_remote_missing`、`remote_meta_missing`、`remote_meta_mismatch`、`remote_size_mismatch`、`fs_id_changed`、`baidu_only` 和 `remote_unreadable`；只比较百度列表可证明的 path、size、md5 和 fs_id，不新增下载接口。
- 新增 `client/src/auto_backup_client/baidu/reconcile_cli.py`，提供 `python -m auto_backup_client.baidu.reconcile_cli remote-objects`，复用真实云端账号解密和百度 token 流程，默认输出脱敏计数、路径 SHA256、size/md5/fs_id 差异和人工处理建议。
- 更新 `client/README.md`，记录 `integration_cli run-resumable --keep-remote -> reconcile_cli remote-objects -> cleanup-resumable` 的真实联调顺序，并明确 `reconcile_cli` 只读、不自动删除、覆盖或重传。
- 新增 `client/tests/test_baidu_reconcile.py` 和 `client/tests/test_baidu_reconcile_cli.py`，覆盖全部差异状态、分页、限速、不可读目录、百度侧额外对象、scope 互斥和脱敏输出。
- 已验证 `client/` 下 `UV_LINK_MODE=copy`、仓库内 `UV_CACHE_DIR`、仓库内临时目录执行新增定向 pytest 通过，8 个测试通过。
- 已验证 `client/` 下同样环境执行全量 `uv run python -m pytest -p no:cacheprovider --basetemp <repo>/.cache/pytest-basetemp-all` 通过，69 个测试通过。

提交摘要：本次提交新增远端对象只读校对 worker 和脱敏 CLI，基于百度 `list/listall` 与本地 `remote_objects`/`upload_sessions` 生成差异报告，覆盖远端缺失、meta 缺失/不匹配、size/fs_id 不一致、百度侧额外对象和不可读目录；本阶段保持只读报告边界，不修改 Go 服务端或云端 schema，不自动删除、覆盖、重传或写入校对结果。

后续待办：

- 在运行时凭据和授权密码可用时，执行真实 `integration_cli run-resumable --keep-remote`，再按 `job_id` 运行 `reconcile_cli remote-objects`，确认 3 个远端对象 `consistent` 后用 `cleanup-resumable` 清理。
- 设计人工确认/修复入口，把报告中的建议动作映射到用户可确认的修复流程；任何删除、覆盖、重传或本地/云端状态写入都必须形成版本记录。
- 远端校对完成后继续推进 P1 备份任务主 UI 与任务模型，不再把联调 CLI 当作用户主流程。

### 进度审计、差异校正与后续排期约束

- 按 `AGENTS.MD` 要求读取近期 Git 历史，重点核对 `c225123`、`77f76f9` 等提交，确认当前分支已完成真实 upload-resumable 联调 CLI、脱敏 outbox 同步 CLI 和百度 `list/listall` 列表能力。
- 对照 `docs/product_spec_v1.3.md`、`docs/roadmap_progress.md`、README、客户端源码和测试，确认当前实际进度是“授权/上传/同步底座已验证，完整备份产品主流程尚未完成”。
- 更新本文件顶部当前阶段说明，明确离完整 v1.3 发布仍缺备份任务主 UI、扫描、去重、7-Zip 加密归档、manifest、缓存、校对 UI、原始数据清理、恢复、打包和最终验收。
- 重写开发排期为 P0-P3 可执行队列，并新增“进度差异审计”和“排期变更记录”，作为后续临时 fix 或新增需求插队时的记录位置。
- 更新 `AGENTS.MD`，要求后续开发必须遵循 `docs/roadmap_progress.md` 的排期；任何临时 fix 或新增需求必须同时更新当前工作项、验收标准、排期变更记录、完成记录和后续待办。
- 更新 README 当前状态，使项目入口文档与实际进度一致。

提交摘要：本次提交完成项目进度审计和排期治理文档更新，明确当前已完成授权、上传、断点续传和同步底座，但距离完整 v1.3 发布仍需完成主备份 UI、扫描、去重、7-Zip/manifest、缓存、校对、清理、恢复和打包验收；后续开发必须按新排期推进，临时 fix 或新增需求必须形成进度和排期文字记录。

后续待办：

- 回到 P0 远端对象校对 worker，优先实现只读差异报告和脱敏测试覆盖。
- 远端校对完成后进入 P1 备份任务主 UI 与任务模型，不再把联调 CLI 当作用户主流程。
- 发布前持续同步 README、客户端 README、产品规格和进度文件，避免入口文档与实际能力再次脱节。

### 脱敏 upload-resumable 一键真实联调 CLI

- 新增 `client/src/auto_backup_client/baidu/integration_cli.py`，提供 `run-resumable` 和 `cleanup-resumable` 两个子命令；`run-resumable` 默认生成临时 archive，执行真实百度容量检查、可恢复上传、同一 SQLite outbox 推送、云端 revision 摘要校验和百度 `filemanager/delete` 清理。
- `cleanup-resumable` 支持按 `job_id` 或 `upload_session_id` 从本地 SQLite 查询 archive、`.meta.json`、`job.index.json` 三类 `remote_objects`，再调用百度官方删除接口清理；输出只包含对象数量、删除 errno 和远端路径 SHA256。
- 扩展 `SQLiteClientStore.list_remote_objects_for_cleanup(...)`，集中封装本批远端对象查询，避免联调 CLI 直接散落 SQL。
- 更新 `client/README.md`，将 `integration_cli run-resumable` 作为阶段验收首选入口，同时保留 `upload-resumable`、`sync-outbox` 和 `cleanup-resumable` 作为分步排障路径；继续明确本阶段只验收 Cloud Sync revision 投影，不要求 `remote_objects` 自动进入 Go `archive_objects` 索引。
- 新增 `client/tests/test_baidu_integration_cli.py`，覆盖默认上传/同步/summary/清理调用顺序、`--keep-remote`、按 `upload_session_id` 清理、冲突/拒绝/可重试计数和错误输出脱敏；测试确认不输出 Device Token、百度 access token、用户密码、本地路径、SQLite 路径或远端真实路径。
- 已验证 `client/` 下 `UV_LINK_MODE=copy`、仓库内 `UV_CACHE_DIR` 执行 `uv sync` 成功。
- 已验证 `client/` 下 `UV_LINK_MODE=copy`、仓库内 `UV_CACHE_DIR`、仓库内临时目录执行 `uv run python -m pytest -p no:cacheprovider --basetemp <repo>/.cache/pytest-basetemp-integration` 通过，61 个测试通过。
- 已验证 `client/` 下 `uv run python -m compileall src tests` 通过。
- 已验证仓库根目录 `git diff --check` 通过。
- 已验证 `cloud-api/` 下 `go version` 返回 `go1.25.10 windows/amd64`；沙箱内 `go list runtime` 仍受标准库路径读取限制失败，按权限流程提升后 `go list runtime` 返回 `runtime`。本轮未修改 Go 服务端代码、迁移或路由，不需要重新编译部署 Go 服务。
- 已执行真实 `integration_cli run-resumable` 联调：上传临时 archive、`.meta.json`、`job.index.json` 成功，`part_count=1`、`uploaded_part_count=1`，本地 outbox 推送 `sync_selected=10`、`sync_sent=10`、`sync_synced=10`、`sync_conflicts=0`、`sync_rejected=0`、`sync_retryable=0`，云端摘要校验 `cloud_summary_verified=10`，百度删除清理 `cleanup_object_count=3`、`cleanup_delete_errno=0`。输出未包含 Device Token、本地路径、SQLite 路径、远端真实路径、payload 明文、百度 token、用户密码或 wrapping key。

提交摘要：本次提交新增脱敏 `integration_cli`，把真实 `upload-resumable -> sync-outbox -> cloud summary -> 百度删除清理` 收口为可复跑 CLI，并补齐按账本清理远端临时对象、测试覆盖和客户端文档；真实云端与真实百度网盘链路已完成一次端到端验证。

后续待办：

- 进入远端对象校对 worker 设计，基于百度 `list/listall` 与本地 `remote_objects` 生成差异状态。
- 为校对结果设计人工确认和修复入口，禁止自动覆盖或删除。
- 如需把校对状态同步到云端新表或新增 API，先规划 Go 迁移和重新部署流程。

### 脱敏 sync-outbox CLI 与百度 list/listall 校对准备

- 新增 `client/src/auto_backup_client/sync_cli.py`，提供 `python -m auto_backup_client.sync_cli sync-outbox`，读取 `LOCAL_SQLITE_PATH` 或 `--sqlite-path`，执行 SQLite 迁移，复用运行时 `CLOUD_API_DEVICE_TOKEN` 或本机 DPAPI Device Token 凭据，并调用现有 `SyncOutboxWorker` 推送 `POST /v1/sync/revisions`。
- `sync-outbox` 输出仅包含 `Device Token 来源`、`selected`、`sent`、`synced`、`conflicts`、`rejected`、`retryable` 和可选 `cloud_summary_verified` 计数；失败输出收敛为脱敏错误类型，不打印本地路径、SQLite 路径、payload、Device Token、百度 token、用户密码或 wrapping key。
- 扩展 `BaiduCloudClient.get_entity_summary(...)` 和 `EntitySummary/RevisionSummary` 模型，封装现有 Go `GET /v1/reconcile/entities/{entity_id}`；`--verify-cloud-summary` 只校验 `revision_id`、`data_version` 和 `canonical_record_sha256`，且同时兼容当前投影与最近 revision 摘要。
- 扩展 `SyncWorkerResult`，在同步成功路径保留本轮 `revision_results`，供 CLI 做云端摘要校验，不改变 worker 原有同步状态回写语义。
- 按官方文档新增百度远端列表能力：`BaiduNetdiskClient.list_dir(...)` 调用 `GET /rest/2.0/xpan/file?method=list`，`BaiduNetdiskClient.list_all(...)` 调用 `GET /rest/2.0/xpan/multimedia?method=listall`，`iter_list_all(...)` 在 `has_more=1` 时使用官方返回的 `cursor` 继续分页。
- 更新 `docs/baidu_netdisk_openapi_reference.md`，记录 2026-06-07 官方 `list/listall` 页面获取方式、官方更新时间、接口路径、关键参数、响应字段、`cursor` 分页和 listall 每分钟 8-10 次官方频控；本项目后续校对 worker 仍按产品规格每分钟不超过 8 次执行。
- 更新 `client/README.md`，固化真实联调顺序：`upload-resumable --check-quota -> sync-outbox --verify-cloud-summary -> 百度 filemanager/delete 清理`，并明确本阶段只验收 Cloud Sync revision 投影，不把 `remote_objects -> archive_objects` 索引联动作为验收条件。
- 新增测试覆盖 `sync-outbox` CLI 成功计数、云端 summary 校验、503 retryable 回写、失败输出不泄露 SQLite 路径、`get_entity_summary` 解析、百度 `list/listall` 参数构造、空目录、`cursor` 分页和不可读错误。
- 已验证 `client/` 下 `UV_LINK_MODE=copy`、仓库内 `UV_CACHE_DIR` 执行 `uv sync` 成功。
- 已验证 `client/` 下 `UV_LINK_MODE=copy`、仓库内 `UV_CACHE_DIR`、仓库内临时目录执行 `uv run python -m pytest -p no:cacheprovider --basetemp <repo>/.cache/pytest-basetemp-sync` 通过，56 个测试通过；此前默认用户 Temp 和 `C:\tmp` 均因当前权限限制导致 pytest 临时目录创建失败，改用仓库内临时目录后通过。
- 已将 pytest 默认临时目录和 `C:\tmp` 权限限制的处理方式补充到 `AGENTS.MD`，后续客户端测试优先使用仓库内 `.cache` 临时目录。
- 已验证 `client/` 下 `uv run python -m compileall src tests` 通过。
- 已验证仓库根目录 `git diff --check` 通过。
- 已验证 `cloud-api/` 下 `go version` 返回 `go1.25.10 windows/amd64`；沙箱内 `go list runtime` 和 `go test ./...` 分别受标准库路径与 Go 构建缓存权限影响失败，提升权限后 `go list runtime` 返回 `runtime`，`go test ./...` 通过。
- 本轮未修改 Go 服务端代码、迁移或路由，不需要重新编译部署 Go 服务；后续真实联调若证明必须修改 Go 服务，必须暂停等待部署更新完成。

提交摘要：本次提交补齐客户端脱敏 `sync-outbox` CLI、云端 revision 摘要校验入口和百度 `list/listall` 远端列表能力，并把官方文档依据、真实联调顺序和现阶段 Go 服务边界写入文档，为下一步真实 `upload-resumable -> sync-outbox -> 百度删除清理` 联调做准备。

后续待办：

- 使用真实云端账号执行 `upload-resumable --check-quota`，上传临时 archive、`.meta.json`、`job.index.json` 并验证本地 SQLite outbox 写入。
- 运行 `sync-outbox --verify-cloud-summary`，确认真实 PostgreSQL revision 投影和本地 outbox/业务表状态一致。
- 用百度 `filemanager/delete` 清理本批远端临时测试文件，并记录脱敏结果。

### sync_outbox 后台同步 worker

- 新增 `client/src/auto_backup_client/baidu/models.py` 中的 `SyncRevisionEvent` 和 `SyncRevisionResult`，并在 `BaiduCloudClient.sync_revisions(...)` 封装 `POST /v1/sync/revisions`，限制单批最多 100 条，解析 `synced`、`duplicate`、`conflict`、`rejected` 等逐条结果。
- 扩展 `client/src/auto_backup_client/sqlite_store.py`，支持读取 `pending` 和到期 `retryable` outbox，标记 `syncing`，成功时业务表与 outbox 同步进入 `synced`，冲突时进入 `sync_conflict`，终止失败进入 `failed_terminal`，可重试错误写入 `retryable`、`retry_count` 和 `next_retry_at`，并将业务表标记为 `sync_failed_retryable`。
- 新增 `client/src/auto_backup_client/sync_worker.py`，按批次读取 outbox 并发送到云端 Cloud Sync API；`synced/duplicate` 视为成功，`conflict` 进入校对状态，`rejected` 不再重试，503 或网络异常按 2s/5s/15s/60s/180s 退避重试。
- worker 不输出 Device Token、本地路径、payload 明文、百度 token、用户密码或 wrapping key；本轮只做本地自动化测试和真实云端 API 客户端契约准备，未执行真实同步联调。
- 新增测试覆盖 Cloud Sync API 请求体与结果解析、503 `retryable_error`、`duplicate` 成功语义、pending/retryable 过滤、worker 单批 100 条上限、成功/冲突/可重试错误对业务表和 outbox 的状态回写。
- 已验证 `client/` 下 `UV_LINK_MODE=copy`、仓库内 `UV_CACHE_DIR` 执行 `uv sync` 成功。
- 已验证 `client/` 下 `UV_LINK_MODE=copy`、仓库内 `UV_CACHE_DIR` 执行 `uv run python -m pytest` 通过，48 个测试通过。
- 已验证 `client/` 下 `UV_LINK_MODE=copy`、仓库内 `UV_CACHE_DIR` 执行 `uv run python -m compileall src tests` 通过。
- 已验证仓库根目录 `git diff --check` 通过。
- 已验证 `cloud-api/` 下 `go version` 返回 `go1.25.10 windows/amd64`，`go list runtime` 通过，`go test ./...` 通过。
- 本轮未修改 Go 服务端契约，不需要服务器重新部署；后续真实同步联调若发现必须修改 Go 服务端，需要暂停等待部署更新完成。

提交摘要：本次提交补齐客户端 `sync_outbox` 后台同步 worker 和 Cloud Sync API 客户端封装，让本地上传账本 revision 可以批量推送到云端，并根据 `synced/duplicate/conflict/rejected` 或可重试错误回写本地 outbox 与业务表同步状态。

后续待办：

- 使用真实云端 API 执行一次受控 `sync_outbox` 同步联调，确认真实 PostgreSQL revision 投影与本地状态回写一致，输出必须脱敏。
- 使用真实云端账号执行 `upload-resumable` 端到端联调，并清理本批远端临时测试文件。
- 获取百度官方 `list/listall` 文档，设计远端对象校对、缺失/不一致标记和人工修复入口。

### 上传账本产品契约审查修复

- 按产品规格复查上一轮 SQLite 上传账本、断点续传和元数据实现，发现并修复 3 个契约偏差：默认 `revision_id` 不是 UUIDv7、百度 `precreate.block_list` 空列表被误解释为需要上传第 0 片、已完成 upload session 重跑会重新进入 precreate/create 链路。
- `client/src/auto_backup_client/sqlite_store.py` 新增不依赖第三方包的 UUIDv7 生成器，默认 `build_version_fields(...)` 生成标准 UUIDv7 revision，继续允许测试或迁移场景显式传入既有 revision。
- `client/src/auto_backup_client/baidu/upload.py` 调整 `PrecreateResult.partseqs_to_upload(...)`：严格以百度返回的 `block_list` 作为缺失分片列表；空列表表示没有缺失分片，不再重复上传第 0 片。
- `client/src/auto_backup_client/baidu/resumable_upload.py` 新增完成态本地短路：当 `upload_sessions` 已是 `remote_created`，`.meta.json` 和 `job.index.json` 均已上传，且 `remote_objects` 中已有 archive、meta、job_index 三类对象时，重跑同一 archive 会直接返回本地完成状态，不再调用百度 `precreate`、分片上传、`create` 或元数据上传。
- 新增测试覆盖默认 revision 为 UUIDv7、空 `block_list` 表示无缺失分片、完成态重跑不再触发百度 API 调用。
- 已验证 `client/` 下 `UV_LINK_MODE=copy`、仓库内 `UV_CACHE_DIR` 执行 `uv run python -m pytest` 通过，39 个测试通过。
- 已验证 `client/` 下 `UV_LINK_MODE=copy`、仓库内 `UV_CACHE_DIR` 执行 `uv run python -m compileall src tests` 通过。
- 已验证仓库根目录 `git diff --check` 通过。
- 已验证 `cloud-api/` 下 `go version` 返回 `go1.25.10 windows/amd64`，`go list runtime` 通过，`go test ./...` 通过。

提交摘要：本次提交修复 review 发现的产品契约偏差，使客户端 revision 默认符合 UUIDv7 要求，断点续传严格按百度缺失分片列表执行，并避免已完成上传重跑时重复触发百度远端创建或冲突。

后续待办：

- 继续下一阶段 `sync_outbox` 后台同步 worker 开发。
- 使用真实云端账号执行 `upload-resumable` 端到端联调，并清理本批远端临时测试文件。

### SQLite 上传账本、断点续传与元数据生成

- 新增 `client/src/auto_backup_client/sqlite_store.py`，提供客户端 SQLite 迁移执行、WAL、foreign keys、显式事务、版本字段构造、规范化记录 SHA256 和业务表与 `sync_outbox` 同事务写入入口。
- 新增 `client/migrations/sqlite/002_upload_state.sql`，落地 `upload_sessions`、`upload_parts` 和 `remote_objects`，包含 `schema_version`、`data_version`、`revision_id`、`updated_at`、`sync_status`、`deleted_at`、`canonical_record_sha256` 和 `last_synced_revision_id`。
- 新增 `client/src/auto_backup_client/baidu/metadata.py`，生成稳定 JSON 的 `.meta.json` 和 `job.index.json`，只包含 archive、job、device、远端路径引用、fs_id 和 hash 等非敏感字段。
- 新增 `client/src/auto_backup_client/baidu/resumable_upload.py`，实现可恢复上传编排：本地账本建档、复用本地 `uploadid` 调用百度 `precreate`、`uploadid` 失效后重新预上传、按百度返回的缺失 `block_list` 上传分片、创建 archive 后上传 `.meta.json` 和 `job.index.json`。
- `canonical_record_sha256` 排除 `revision_id`、schema/data version、更新时间、同步状态、`local_archive_path`、`uploadid` 和本地错误消息，避免本地控制字段或敏感路径影响云端业务内容校对。
- 扩展 `client/src/auto_backup_client/baidu/upload_cli.py`，新增 `upload-resumable` 命令，默认读取 `LOCAL_SQLITE_PATH`，输出账号 ID、token version、对象 hash、分片计数和 fs_id，不输出 access token、refresh token、wrapping key、Device Token、本地敏感路径或本地 SQLite 路径。
- 扩展 `client/src/auto_backup_client/settings.py` 和 `client/README.md`，补充 `LOCAL_DATA_DIR`、`LOCAL_SQLITE_PATH`、`LOCAL_CACHE_DIR` 和本地 SQLite 上传账本说明。
- 更新 `docs/baidu_netdisk_openapi_reference.md`，说明当前已补齐本地账本、续传和元数据入口，远端 `list/listall` 校对仍属后续阶段。
- 新增测试覆盖 SQLite 迁移幂等、事务回滚、业务表与 `sync_outbox` 同事务写入、规范化 hash 排除控制/本地字段、元数据稳定 JSON 脱敏、复用 `uploadid` 续传、空缺失分片列表不重复上传、`uploadid` 失效后重新 `precreate` 和 `upload-resumable` 输出脱敏。
- 已验证 `client/` 下 `UV_LINK_MODE=copy`、仓库内 `UV_CACHE_DIR` 执行 `uv sync` 成功。
- 已验证 `client/` 下 `UV_LINK_MODE=copy`、仓库内 `UV_CACHE_DIR` 执行 `uv run python -m pytest` 通过，37 个测试通过。
- 已验证 `client/` 下 `UV_LINK_MODE=copy`、仓库内 `UV_CACHE_DIR` 执行 `uv run python -m compileall src tests` 通过。
- 已验证仓库根目录 `git diff --check` 通过。
- 已验证 `cloud-api/` 下 `go version` 返回 `go1.25.10 windows/amd64`，`go list runtime` 通过，`go test ./...` 通过。
- 本轮未执行真实 `upload-resumable` 上传联调；入口已准备好，后续执行时必须使用真实云端账号和真实百度 API 上传临时 archive、`.meta.json`、`job.index.json`，验证后调用百度删除接口清理远端测试文件。

提交摘要：本次提交补齐客户端本地 SQLite 上传账本、断点续传编排和远端元数据生成能力，让 archive 上传可以在本地记录 `uploadid`、分片状态、远端对象和 outbox revision，并新增 `upload-resumable` 真实联调入口。

后续待办：

- 实现客户端 `sync_outbox` 后台同步 worker，把本地 revision 批量推送到 Go 云端 Cloud Sync API，并处理幂等成功、冲突和重试。
- 使用真实云端账号执行 `upload-resumable` 端到端联调，并清理本批远端临时测试文件。
- 获取百度官方 `list/listall` 文档，设计远端对象校对、缺失/不一致标记和人工修复入口。

### 百度上传核心链路与真实批测入口

- 新增 `client/src/auto_backup_client/baidu/upload.py`，封装百度网盘容量读取、文件分片规划、远端路径构造、`precreate`、`locateupload`、`superfile2`、`create` 和 `filemanager/delete`。
- 远端路径按 `/apps/{appname}/backups/{yyyy}/{MM}/{dd}/{device_id}/{job_id}/archives/{archive_seq}-{archive_sha256}.7z` 构造，继续禁止使用 `archive_sha256` 前缀作为目录分桶。
- 上传核心库固定 `User-Agent: pan.baidu.com`，`rtype=0`，`block_list` 按分片顺序提交；返回和 CLI 输出不包含 access token、refresh token、wrapping key、Device Token 或本地敏感路径。
- 新增 `client/src/auto_backup_client/baidu/upload_cli.py`，提供 `quota`、独立 `uinfo`、`upload-file` 和 `real-batch` 真实联调入口，默认复用本机 DPAPI Device Token 凭据和 account 级 KDF 参数。
- `real-batch` 会生成小文件和跨 4 MiB 分片文件，执行 `quota -> small upload -> multipart upload -> conflict`，并默认调用百度 `filemanager/delete` 清理本批远端测试文件。
- 真实联调发现按 2026-05-15 官方“获取用户信息”页调用 `GET https://pan.baidu.com/rest/2.0/xpan/nas?method=iotqueryuinfo`，当前真实授权应用或路径返回 HTTP 404；因此已将 `uinfo` 从 `real-batch` 必经步骤拆出，只保留为独立排查命令。
- 新增 `docs/baidu_netdisk_openapi_reference.md`，记录百度官方用户信息、容量、预上传、获取上传域名、分片上传、创建文件和删除接口的来源、官方更新时间、关键参数、本轮实现边界与 `iotqueryuinfo` 真实差异。
- 在 `AGENTS.MD` 补充 Git 历史阅读约束、官方 API 文档获取顺序，以及百度上传链路真实 API/真实删除清理验收约束。
- 新增客户端测试覆盖远端路径布局、分片 MD5 顺序、`precreate` 待上传分片序号处理，以及 `real-batch` 不再依赖 `uinfo` 探针的控制流。
- 已验证 `client/` 下 `UV_LINK_MODE=copy`、仓库内 `UV_CACHE_DIR` 执行 `uv sync` 成功。
- 已验证 `client/` 下提升权限执行 `uv run python -m pytest` 通过，27 个测试通过；提升权限原因是当前沙箱内无法启动 `.venv\Scripts\python.exe` 子进程，问题已沉淀到 `AGENTS.MD`。
- 已验证 `client/` 下提升权限执行 `uv run python -m compileall src tests` 通过。
- 已验证 `cloud-api/` 下 `go version` 返回 `go1.25.10 windows/amd64`；沙箱内 `go list runtime` 仍受标准库读取权限影响，提升权限后 `go list runtime` 返回 `runtime`，`go test ./...` 通过。
- 已使用本机 DPAPI Device Token 凭据和真实百度授权账号执行 `real-batch`，真实百度 API 完成容量检查、小文件上传、跨 4 MiB 分片上传、同路径冲突检测和 `filemanager/delete` 清理；清理返回 `cleanup_delete_errno=0`，远端测试文件已删除。

提交摘要：本次提交落地百度上传核心库和真实批测 CLI，将官方但当前 404 的 `iotqueryuinfo` 从上传批测必经链路拆出，让上传验收聚焦容量、预上传、分片上传、创建文件、冲突检测和删除清理主链路。

后续待办：

- 后续补齐上传状态 SQLite 表、断点续传、uploadid 恢复、`.meta.json`/`job.index.json` 生成和远端校对流程。
- 继续保持 `uinfo` 仅作为独立探针；除非后续官方或真实应用权限修正其 404 行为，不得重新作为上传主链路前置条件。

### 扫码授权、本机设备凭据与服务功能验证

- 新增 `client/src/auto_backup_client/device_credentials.py`，当前设备没有运行时 `CLOUD_API_DEVICE_TOKEN` 时会自动注册真实云端设备，并将 Device Token 保存到本机 DPAPI 凭据文件。
- PySide6 百度设置页改为扫码确认授权优先：页面生成扫码授权二维码，不再要求用户输入用户码；扫码确认后 UI 会自动完成密文 token 入库和本机 KDF 参数保存。
- CLI 真实联调入口改为优先使用运行时 Device Token，否则复用本机 DPAPI Device Token 凭据；本机无凭据时自动注册并保存当前设备，显式 `--register-ephemeral-device` 才使用一次性临时设备。
- 修复客户端刷新租约解析：真实云端第二设备并发获取 `refresh-lease` 返回 409 时，客户端现在解析为 `BaiduRefreshLease(acquired=False)` 业务结果，而不是泛型 `CloudAPIError`。
- 更新 `.env.example`、`client/README.md` 和 `client/docs/baidu_auth_manual_validation.md`，补充自动注册当前设备、Device Token DPAPI 凭据路径、扫码授权和 CLI 复用本机凭据说明。
- 已使用 PySide6 UI 完成当前设备真实百度扫码授权，新授权账号 token 有效且已选中；本机 KDF store 可在新进程中用同一授权密码解密云端密文 token。
- 已验证真实 `https://backup.baichengedu.com/v1/healthz` 与 `/v1/readyz` 均返回 HTTP 200。
- 已验证真实云端账号列表、账号选择、密文 token 元数据读取和刷新租约互斥：第一设备获取刷新租约成功，第二设备并发获取返回 `acquired=false` 且租约 ID 与第一设备一致。
- 已用解密后的真实百度 access token 验证百度网盘 `uinfo` 和 `quota` 接口均返回 HTTP 200，确认授权 token 可被百度真实 API 接受。
- 已验证 `client/` 下 `UV_LINK_MODE=copy`、仓库内 `UV_CACHE_DIR` 执行 `uv sync` 成功。
- 已验证 `client/` 下 `UV_LINK_MODE=copy`、仓库内 `UV_CACHE_DIR` 执行 `uv run pytest` 通过，21 个测试通过。
- 已验证 `client/` 下 `UV_LINK_MODE=copy`、仓库内 `UV_CACHE_DIR` 执行 `uv run python -m compileall src tests` 通过。
- 已验证 `cloud-api/` 下 `go version` 返回 `go1.25.10 windows/amd64`，`go list runtime` 通过，`go test ./...` 通过。

提交摘要：本次提交完成当前设备扫码授权、本机 Device Token DPAPI 凭据持久化、CLI/UI 凭据复用和真实服务功能验证，并修复客户端刷新租约 409 响应解析，让后续百度上传链路可以直接复用已验证的账号、token 解密和租约互斥能力。

后续待办：

- 开始实现百度上传核心链路：容量/账号信息复用、预上传 `precreate`、分片上传 `superfile2`、创建文件 `create` 和失败重试边界。
- 上传测试继续只输出脱敏状态，不输出 access token、refresh token、wrapping key、Device Token 或敏感本地/远端路径。
- 后续上传远端目录必须遵循 `/apps/{appname}/backups/{yyyy}/{MM}/{dd}/{device_id}/{job_id}/` 组织规则。

### password 模式 KDF 参数持久化与恢复解密

- 新增 `client/src/auto_backup_client/baidu/kdf_store.py`，提供 `PasswordKDFRecord` 和 `PasswordKDFStore`，按 `account_id` 保存 password 模式 KDF salt、Argon2id 参数、token version 和时间元数据。
- Windows 默认使用当前用户 DPAPI 保护 KDF store 文件；非 Windows 或自动化测试只有显式 `allow_plaintext=True` 或 `AUTO_BACKUP_BAIDU_KDF_STORE_ALLOW_PLAINTEXT=true` 时才允许明文测试存储。
- `BaiduAuthWorkflow.complete_password_session(...)` 改为返回 `PasswordAuthCompletion`，完成真实云端授权后立即保存对应账号的 KDF 参数，不保存用户密码或 wrapping key。
- 新增 `BaiduAuthWorkflow.decrypt_password_token(...)`，从云端读取密文 token，并基于本地持久化 KDF 参数和用户输入密码重新派生 wrapping key，在本地内存中解密 token。
- 真实联调 CLI `device-code` 完成授权后提示本机已保存 KDF 参数；新增 `token-check` 命令，只输出账号 ID、token version、过期时间、token type 和 scope 等脱敏元数据，不输出 access token 或 refresh token。
- PySide6 百度设置页完成授权后显示 KDF 参数已保存，并新增“验证解密”按钮，用于选中账号后读取云端密文 token 并验证本地 KDF 参数可恢复解密。
- 更新 `client/README.md` 和 `client/docs/baidu_auth_manual_validation.md`，补充 KDF store 默认路径、DPAPI 保护、明文测试 opt-in、CLI `token-check` 和 UI 验证解密步骤。
- 新增客户端测试覆盖 plaintext store 必须显式 opt-in、Windows DPAPI store 往返、完成授权保存 KDF 参数，以及模拟客户端重启后用同一密码解密云端密文 token。
- 已验证 `client/` 下 `UV_LINK_MODE=copy`、仓库内 `UV_CACHE_DIR` 执行 `uv sync` 成功。
- 已验证 `client/` 下提升权限执行 `uv run python -m compileall src tests` 通过；提升权限原因是当前 `.venv` 指向 uv 受管 Python 目录，沙箱内读取用户目录受限。
- 已验证 `client/` 下提升权限执行 `uv run pytest` 通过，15 个测试通过。
- 已验证 `cloud-api/` 下 `go version` 返回 `go1.25.10 windows/amd64`。
- 已验证 `cloud-api/` 下提升权限执行 `go list runtime` 通过；提升权限原因是沙箱内读取 Go 标准库路径受限。
- 已验证 `cloud-api/` 下提升权限执行 `go test ./...` 通过。

提交摘要：本次提交补齐 password 模式 KDF 参数持久化和恢复解密入口，客户端完成授权后会按账号保存受 DPAPI 保护的 KDF 材料，并可在 CLI 或 PySide6 UI 中用同一授权密码重新派生 wrapping key 验证云端密文 token 本地解密能力。

后续待办：

- 对真实云端账号重新完成一次带 KDF 参数持久化的新授权，或提供受控 re-encrypt 流程补齐已入库旧账号的本机 KDF 材料。
- 使用真实云端 `token-check` 或 UI “验证解密”验证重启后的 token 本地解密。
- 在 token 可本地解密后，继续真实百度容量/用户信息、预上传、分片上传和 create 链路联调。

### Windows 系统 Go 工具链修复

- 按用户要求停止项目内绕过方式，直接排查系统默认 `C:\Program Files\Go\bin\go.exe`。
- 确认 `C:\Program Files\Go\src\runtime` 等标准库目录和文件实际存在；早期 `package runtime is not in std` 现象与沙箱无法读取用户级 Go 配置/缓存权限有关，不能作为系统 Go 缺标准库文件的最终结论。
- 使用默认系统 Go 1.26.2 运行 `go list runtime` 可通过，但清缓存后 `go test ./...` 在标准库 `internal/profile` 编译阶段触发 compiler internal error。
- 下载官方 `go1.26.3.windows-amd64.msi` 并验证 Google LLC 签名有效；普通非管理员静默 MSI 安装日志返回 1603，未替换系统 Go。
- 通过管理员 UAC 安装 Go 1.26.3 后，注册表和 `go version` 均显示 1.26.3；但清理 Go 构建缓存后，标准库 `math/big`、`vendor/golang.org/x/net/idna` 编译仍触发 compiler/runtime 崩溃。
- 由于本项目 `cloud-api/go.mod` 要求 `go 1.25.0`，改用官方维护的 Go 1.25 补丁线；下载并验签 `go1.25.10.windows-amd64.msi`，通过管理员 UAC 卸载 1.26.3 并安装 1.25.10。
- 已确认注册表显示 `Go Programming Language amd64 go1.25.10`，`go version` 返回 `go1.25.10 windows/amd64`。
- 已验证在 `cloud-api/` 下执行 `go clean -cache` 后，`go list runtime` 通过，`go test ./...` 通过。
- 将系统 Go 1.26.x 冷缓存编译标准库崩溃、非管理员 MSI 返回 1603 但不会替换系统 Go 的问题沉淀到 `AGENTS.MD`。

提交摘要：本次提交记录并固化 Windows 系统 Go 工具链修复结果，系统默认 Go 已从不稳定的 1.26.x 切换到与项目 `go.mod` 匹配的官方 Go 1.25.10，恢复 `cloud-api/` 的本地 Go 常规验收。

后续待办：

- 后续 Windows 本地 Go 验收优先使用官方 Go 1.25 最新补丁线，并在测试前执行 `go version` 与 `go list runtime`。
- 继续补齐 password 模式 KDF salt/凭据持久化。

### 初始化项目仓库与产品文档

- 完成 Git 仓库初始化。
- 新增 `AGENTS.MD`，记录项目记忆、PowerShell UTF-8 约束、提交规则、敏感信息规则和架构约束。
- 新增 `docs/product_spec_v1.3.md`，落地完整产品与技术规格。
- 新增 `.env.example`，提供本地数据库、云端 API、PostgreSQL 和百度开放平台配置占位。
- 新增 `.gitignore`，排除 `.env`、本地数据库、日志、缓存和密钥目录。
- 新增 `README.md`、`.editorconfig` 和 `.gitattributes`。

### 修正 Git 换行策略

- 发现初始化提交时 Git 提示部分文本文件后续可能被 CRLF 替换。
- 已将仓库本地 `core.autocrlf` 设置为 `false`。
- 已在 `.gitattributes` 中使用 `* text=auto eol=lf`，并对常见文本文件类型明确指定 LF。
- 已将该问题和后续约束补充到 `AGENTS.MD`。

下一步计划：搭建 Python 项目骨架、配置加载、日志脱敏和基础目录结构。

### Go 云端同步服务与 outbox 同步边界

- 将产品规格中的云端服务从 FastAPI + PostgreSQL 调整为 Go + PostgreSQL。
- 在产品规格第 5 节补充客户端 `sync_outbox`、Go Cloud Sync API、Device Token、PostgreSQL 表、幂等写入、冲突检测和查询接口的详细契约。
- 新增 `cloud-api/cmd/cloud-api` 单二进制入口，使用 chi 路由、pgx/pgxpool 连接 PostgreSQL。
- 新增设备注册接口，云端只保存 Device Token 哈希，后续接口通过 Bearer Token 认证。
- 新增批量 revision 同步接口，支持 `entity_id + revision_id` 幂等、`data_version` 冲突检测和逐条同步结果返回。
- 新增内容对象、归档对象和校对摘要查询接口。
- 新增 PostgreSQL 迁移 `cloud-api/migrations/postgres/001_cloud_sync.sql`，包含 `devices`、`cloud_entities`、`entity_revisions`、`content_objects`、`archive_objects`。
- 新增 SQLite 迁移 `client/migrations/sqlite/001_sync_outbox.sql`，明确客户端本地 `sync_outbox` 表契约。
- 新增 `cloud-api/sqlc.yaml` 和 SQL 查询文件作为后续 sqlc 生成入口；当前环境缺少 sqlc，保留可编译的 pgx 手写数据层。
- 新增 Go 单元测试，覆盖设备认证、重复 revision 幂等、版本冲突、内容查询、归档查询和非法 payload 拒绝。

提交摘要：Go 云端 API 已具备 v1.3 outbox 同步接收、幂等写入、冲突检测和基础查询能力；客户端仍禁止直连 PostgreSQL。

后续待办：

- 搭建 Python 客户端项目骨架、配置加载和日志脱敏。
- 在客户端 SQLite 仓储层实现业务写入与 `sync_outbox` 同事务提交。
- 安装并验证 sqlc 后，可在单独提交中将 Go 数据层切换为生成代码。

### Go 服务目录整理与数据库一致性规则修正

- 将 Go 云端服务的 `go.mod`、`go.sum`、`sqlc.yaml`、`cmd/`、`internal/` 和 PostgreSQL 迁移统一移动到 `cloud-api/`。
- 将客户端本地 SQLite `sync_outbox` 迁移移动到 `client/migrations/sqlite/001_sync_outbox.sql`，明确其不属于 Go 服务端迁移。
- 将 Go module 调整为 `auto_backup_bdnetdesk/cloud-api`，并修正服务入口 import。
- 新增 `cloud-api/.env.example`，说明 Go 服务端通过 `POSTGRES_DSN` 或 PostgreSQL 分项环境变量连接目标 PostgreSQL。
- 更新 README 和产品规格，补充二进制部署到服务器后的 systemd `EnvironmentFile=/etc/auto-backup-bdnetdesk/cloud-api.env` 配置方式。
- 更新产品规格的一致性口径：当前云端数据库是 `cloud_entities + entity_revisions` 的 revision 投影与审计层，不等同于所有本地业务表已建立同名云端物理表；校对以 `entity_id`、`revision_id`、`data_version`、`canonical_record_sha256` 为准。
- 修正 `AGENTS.MD` 和本文件的进度规则，要求功能完成记录在提交前写入，并与代码和文档进入同一个 commit。
- 发现 `go build ./cmd/cloud-api` 在 Windows 下会生成 `cloud-api.exe`，已删除本地构建产物并在 `.gitignore` 中忽略 `*.exe`，同时将该约束沉淀到 `AGENTS.MD`。

提交摘要：Go 云端服务目录已收拢，部署配置来源和数据库一致性边界已明确，进度文件提交规则已改为提交前更新。

后续待办：

- SQLite schema 阶段补齐本地核心业务表，并决定云端继续使用 JSONB 投影、增加同名物理表，或增加视图/索引表。
- 后续构建检查优先使用 `go build -o` 输出到临时目录，避免生成本地 `.exe` 污染工作区。
- 继续搭建 Python 客户端项目骨架、配置加载和日志脱敏。

### Go 服务端部署构建脚本适配

- 新增根目录 `go_build.ps1`，默认从仓库根目录自动定位 `cloud-api/` Go module，并自动识别唯一的 `cmd/cloud-api/main.go` 服务入口。
- 默认构建目标为 `linux/amd64`，启用 `CGO_ENABLED=0`、`-trimpath`、`-buildvcs=false` 和 `-ldflags="-s -w"`，生成适合服务器部署的精简单二进制。
- 默认输出路径为 `dist/cloud-api/linux-amd64/cloud-api`，二进制名称与 README 和产品规格中的 systemd `ExecStart=/opt/auto-backup-bdnetdesk/cloud-api` 保持一致。
- 支持 `-ModuleDir`、`-ServiceName`、`-OutputDir`、`-OutputName`、`-GoOS`、`-GoArch`、`-NameSource` 和 `-CompressWithUpx` 参数，保留通用构建脚本能力。
- 将 Go 构建缓存统一放入仓库根目录 `.cache/go-build` 和 `.cache/go-mod`，并在 `.gitignore` 中忽略 `.cache/` 与 `dist/`。
- 在 `.gitattributes` 中补充 `*.ps1 text eol=lf`，保持 PowerShell 脚本符合项目 LF 约束。
- 更新 README，补充从仓库根目录执行 `.\go_build.ps1` 生成服务端部署二进制的说明。
- 将本轮遇到的 Windows PowerShell 5.1 UTF-8 文件读取和 .NET API 兼容性问题沉淀到 `AGENTS.MD`。
- 已验证在仓库根目录执行 `.\go_build.ps1` 成功生成 `dist/cloud-api/linux-amd64/cloud-api`。
- 已验证在 `cloud-api/` 下执行 `go test ./...` 通过。

提交摘要：根目录通用 Go 构建脚本已适配本项目 `cloud-api/` 服务端模块，可直接生成 Linux amd64 部署二进制，构建缓存和产物已隔离并忽略。

后续待办：

- 后续发布阶段可增加二进制 SHA256 校验文件、版本号注入和部署包打包流程。
- 继续搭建 Python 客户端项目骨架、配置加载和日志脱敏。

### Go 云端百度网盘授权管理

- 已将本地 Git 主分支从 `master` 重命名为 `main`，当前仓库没有远端，后续添加 GitHub remote 后再推送 `main` 并在 GitHub 切换默认分支。
- 新增 PostgreSQL 迁移 `cloud-api/migrations/postgres/002_baidu_auth.sql`，包含 `baidu_accounts`、`baidu_auth_sessions`、`baidu_account_device_bindings` 和 `baidu_token_refresh_leases`。
- 新增百度网盘设备码授权和授权码回调 API，支持授权 session 创建、轮询、完成、回调记录、账号列表、账号选择、密文 token 读取/更新和刷新租约。
- 服务端完成百度 OAuth token 交换后立即用 password wrapping key 或 RSA 公钥加密 token，仅保存密文 envelope、`encryption_method`、`token_version` 和过期时间。
- 百度授权 callback 只记录 `code/state/error`，最终由已认证客户端调用 complete 接口完成 token 交换和加密入库。
- 密文 token 响应固定包含 `encryption_method`，客户端可据此选择用户密码派生密钥或 RSA 私钥解密；服务端不提供解密 token 接口。
- 多设备可选择同一个百度账号，并通过刷新租约和 `expected_token_version` 避免 refresh token 并发覆盖。
- 新增 fake Baidu OAuth 单元测试，覆盖设备码 session、授权码 callback、password/RSA 加密元数据、多设备选择、刷新租约互斥、token version 冲突和敏感信息不回显。
- 更新 `.env.example`，移除客户端百度 App Secret 和 token 填写项；更新 `cloud-api/.env.example`，新增 `backup.baichengedu.com`、百度开放平台和 OAuth 端点配置。
- 更新 README 和产品规格，明确客户端不收集百度账号密码，账号密码只允许在百度官方 `openapi.baidu.com` 页面输入。
- 新增 `docs/deployment_nginx_backup_baichengedu.md`，记录 `backup.baichengedu.com`、nginx 反代、百度回调和服务端环境配置。
- 新增 `scripts/generate_baidu_rsa_keypair.ps1`，作为 RSA 备选模式的部署密钥生成脚本，输出目录 `deploy-only/` 已被 Git 忽略。
- 已验证在 `cloud-api/` 下执行 `go test ./...` 通过。
- 已验证在仓库根目录执行 `.\go_build.ps1` 成功生成 `dist/cloud-api/linux-amd64/cloud-api`。
- 已验证 `git diff --check` 通过。

提交摘要：Go 云端服务已具备百度网盘云端授权管理能力，百度 token 不再需要客户端手工填写，服务端只保存密文 token，多设备共享账号通过选择绑定、刷新租约和 token 版本锁保证一致性。

后续待办：

- 在 Python 客户端实现百度账号选择、新增授权 UI、设备码二维码展示、授权轮询和密文 token 本地解密。
- 在客户端实现 refresh token 刷新流程：先获取云端刷新租约，本地解密 refresh token，调用百度 token 接口后用 `PUT /v1/baidu/accounts/{account_id}/token` 回写新密文。
- 使用真实百度开放平台 App Key 在服务器上手动验证设备码端点、授权码回调、用户信息端点和 scope 配置。
- 后续上传阶段继续实现百度预上传、分片上传和创建文件流程。

### GitHub 公开仓库发布与 LGPL-2.1 授权

- 确认 `gh` 已登录 GitHub 账号 `tiankongzhise`，Git 操作协议为 SSH。
- 确认 `ssh -T git@github.com` 返回 GitHub SSH 认证成功提示。
- 确认本地工作区发布前干净，且没有未跟踪文件。
- 新增标准 LGPL-2.1 `LICENSE` 文件。
- 更新 README，补充公开发布状态、许可证入口和 LGPL-2.1 授权说明。
- 发现提交后 Git 自动维护传入不兼容 `--detach` 参数，已在仓库本地设置 `maintenance.auto=false`，并将问题沉淀到 `AGENTS.MD`。
- 已创建 GitHub 公开仓库 `tiankongzhise/auto_backup_bdnetdesk`。
- 已将本地 `origin` 设置为 SSH 地址 `git@github.com:tiankongzhise/auto_backup_bdnetdesk.git`。
- 已推送本地 `main` 分支到 `origin/main`，GitHub 默认分支确认为 `main`。
- 已配置仓库简介：Windows 桌面端百度网盘加密备份工具，使用本地 SQLite、Go 云端 API 和 PostgreSQL 实现多设备同步、加密归档与最终一致校对。
- 已配置 topics：`backup`、`baidu-netdisk`、`encrypted-backup`、`golang`、`postgresql`、`pyside6`、`sqlite`、`windows`。
- 已核验 GitHub 识别许可证为 `GNU Lesser General Public License v2.1`，仓库可见性为 `PUBLIC`。

提交摘要：本次提交补充公开发布所需的 LGPL-2.1 授权文件、README 许可证说明和发布进度记录，为后续通过 SSH 推送到 GitHub 公开仓库做准备。

提交摘要：本次提交记录 Git for Windows 提交后自动维护的 `--detach` 参数兼容性问题，并通过仓库本地 `maintenance.auto=false` 约束后续提交流程，避免将维护报错误判为提交失败。

提交摘要：本次提交记录 GitHub 公开仓库已创建、SSH remote 已配置、`main` 已推送、仓库简介与 topics 已配置，并把当前工作项切换到客户端百度授权与上传流程。

后续待办：

- 继续 Python 客户端百度授权 UI、密文 token 本地解密和百度上传流程开发。

### Go 云端服务宝塔部署配置读取修复

- 确认宝塔部署失败原因：原 Go 服务只通过 `os.Getenv` 读取进程环境变量，不会主动读取 `.env` 文件；宝塔面板变量未进入服务进程时，服务回退到默认 `auto_backup_user/auto_backup_bdnetdesk` PostgreSQL 配置。
- 新增 `--env-file /path/to/.env` 启动参数和 `CLOUD_API_ENV_FILE` 环境变量，支持显式加载服务器环境文件。
- 未显式指定环境文件时，服务自动尝试当前工作目录、二进制所在目录下的 `cloud-api.env`/`.env`，Linux 下额外尝试 `/etc/auto-backup-bdnetdesk/cloud-api.env`。
- `.env` 加载只填充缺失变量，不覆盖 systemd、宝塔或 Shell 已经注入的非空进程环境变量。
- 新增 `APP_ENV`、`LOG_LEVEL`、监听地址、环境文件加载情况、PostgreSQL 配置来源和脱敏连接摘要的启动日志。
- PostgreSQL 连接池创建、Ping 失败日志补充 `postgres_config_source`、`postgres_host`、`postgres_port`、`postgres_database`、`postgres_user`、`postgres_sslmode`、`postgres_password_set` 和 `postgres_defaulted_fields`，不输出完整 DSN 或密码。
- `CLOUD_API_ADDR` 支持裸端口兼容，配置为 `9321` 时自动归一化为 `:9321`；文档仍推荐反代部署使用 `127.0.0.1:9321`。
- 更新 `cloud-api/.env.example`、README、产品规格和 `backup.baichengedu.com` 部署文档，补充宝塔 `--env-file` 启动方式、`APP_ENV=production` 作用和日志排查字段。
- 将宝塔环境变量未进入 Go 服务进程的问题沉淀到 `AGENTS.MD`。
- 新增配置加载单元测试，覆盖显式 env 文件、进程环境优先、`POSTGRES_DSN` 优先和裸端口归一化。
- 已验证在 `cloud-api/` 下执行 `go test ./...` 通过。
- 已验证在仓库根目录执行 `.\go_build.ps1` 成功生成 `dist/cloud-api/linux-amd64/cloud-api`。
- 已验证 `git diff --check` 通过。

提交摘要：Go 云端服务现已支持宝塔等面板部署场景下自行读取指定 `.env` 文件，并在启动和 PostgreSQL 连接失败日志中输出脱敏配置摘要，便于确认环境文件是否加载、实际连接到哪个数据库用户和库名。

后续待办：

- 宝塔部署时优先使用 `cloud-api --env-file /实际路径/.env`，并将 `APP_ENV` 设置为 `production`、`CLOUD_API_ADDR` 设置为 `127.0.0.1:9321` 或实际反代端口。
- 使用服务器真实 PostgreSQL 配置重新启动服务，并通过日志确认 `env_files_loaded` 非空、`postgres_config_source` 和 `postgres_user/postgres_database` 与预期一致。
- 继续 Python 客户端百度授权 UI、密文 token 本地解密和百度上传流程开发。

### Python 客户端百度授权核心库

- 按用户约束将 Python 客户端源码、测试、依赖声明、锁文件和客户端专用说明集中放入 `client/` 目录，未在仓库根目录散布客户端代码。
- 新增 `client/pyproject.toml` 和 `client/uv.lock`，使用 uv 管理客户端依赖；新增 `.venv/`、`.pytest_cache/` 和 `*.egg-info/` 忽略规则。
- 在 `AGENTS.MD` 中新增 Python 客户端 uv 依赖管理约束：新增依赖用 `uv add`，移除依赖用 `uv remove`，同步使用 `uv sync`，不得用 `pip install` 变更依赖。
- 将当前环境 uv hardlink 失败回退复制的问题沉淀到 `AGENTS.MD`，后续 uv 命令优先设置 `UV_LINK_MODE=copy`。
- 新增客户端配置读取与敏感字段脱敏工具，覆盖 Device Token、access token、refresh token、App Secret、wrapping key、密文 token envelope 等字段。
- 新增 Go 云端百度授权 API 客户端，封装账号列表、账号选择、授权 session 创建/轮询/完成、密文 token 读取/回写和刷新租约接口。
- 新增百度密文 token 本地处理能力，支持 password 模式 Argon2id 派生 wrapping key、AES-256-GCM envelope 解密和刷新后重新加密；RSA envelope 解密入口已预留。
- 新增 refresh token 核心流程：先获取云端刷新租约，再本地解密 refresh token，调用百度 token 端点，最后携带 `expected_token_version` 回写新密文 token。
- 新增客户端手动验收文档 `client/docs/baidu_auth_manual_validation.md`，明确真实 Device Token、百度 App Secret、access token、refresh token 和用户密码只允许运行时输入或放入 Windows DPAPI/凭据管理器，不得写入仓库文件。
- 新增 8 个客户端单元测试，覆盖 API 契约、结构化错误、password token envelope 往返、Argon2id 派生、envelope 方法校验、refresh 租约与版本回写、配置读取和敏感字段脱敏。
- 已验证在 `client/` 下执行 `UV_LINK_MODE=copy uv sync` 成功。
- 已验证在 `client/` 下执行 `UV_LINK_MODE=copy uv run pytest` 通过。
- 已验证 `git diff --check` 通过。

提交摘要：Python 客户端已具备百度授权核心库、uv 依赖管理、密文 token 本地解密/重加密和 refresh token 租约回写流程，后续 PySide6 UI 可直接复用该核心库完成账号选择、新增授权、二维码展示和授权状态轮询。

后续待办：

- 基于客户端核心库实现 PySide6 百度设置页，集中放在 `client/` 目录。
- 使用真实服务器验证设备码授权、授权完成和账号选择链路；如需修改 Go 服务，暂停等待人工重新部署后再继续。
- 评估 refresh token 刷新是否应迁移到 Go 服务端受控执行；若需要服务端改造，单独提交并等待人工部署。
- 后续继续实现百度预上传、分片上传和创建文件流程。

### PySide6 百度设置页与真实云端授权联调入口

- 新增 `client/src/auto_backup_client/baidu/auth_workflow.py`，作为 UI/CLI 复用的百度授权流程控制器，封装账号加载、账号选择、设备码 session 创建、轮询和 password wrapping key 完成授权。
- 新增 `client/src/auto_backup_client/ui/baidu_settings.py`，实现 PySide6 百度设置页：真实云端连接信息、账号表、选择账号、设备码授权、用户码、授权 URL、二维码、轮询状态、授权完成反馈和基础上传参数控件。
- 新增 `client/src/auto_backup_client/baidu/real_auth_cli.py`，用于真实云端联调：`health`、`accounts`、`select`、`device-code`，支持运行时 Device Token 和进程内临时设备注册，不写入敏感信息。
- 新增 Python 客户端依赖 `PySide6`、`qrcode[pil]`，并在 `client/pyproject.toml` 中配置清华 PyPI 镜像，解决 PySide6 默认源下载过慢问题；`uv.lock` 已同步。
- 新增客户端测试 `test_baidu_auth_workflow.py`，覆盖授权状态文案、token 有效性判断和 password wrapping material 派生；保留自动化测试只覆盖本地逻辑，不以模拟云端 API 作为真实联调验收。
- 新增 `BaiduCloudClient.register_device` 封装和 `DeviceRegistration` 模型，真实联调可在进程内注册临时设备。
- 真实云端 `/v1/healthz` 与 `/v1/readyz` 旧版本均返回 200，但设备注册返回 500；服务器日志确认 `ERROR: relation "devices" does not exist (SQLSTATE 42P01)`。
- 新增服务端内置 PostgreSQL 迁移，二进制内嵌 `cloud-api/migrations/postgres/*.sql`，并用 `schema_migrations` 记录迁移名称和 SHA256。
- `GET /v1/readyz` 已升级为同时检查 PostgreSQL 可连接和关键表/列存在；缺少 `devices`、`baidu_accounts` 等 schema 时返回 `schema_not_ready` 和缺失清单。
- `cloud-api serve` 启动时会自动检查 PostgreSQL schema；缺少关键表/列或检查失败时自动执行内置迁移并复查，复查失败才拒绝启动。
- 内置迁移执行使用 PostgreSQL 事务级 advisory lock，避免多实例同时启动时重复抢写。
- `cloud-api migrate --env-file /实际路径/.env` 保留为排障或人工修复入口，不再作为二进制正常部署初始化前置条件。
- 更新 README、产品规格、部署文档、客户端说明和手动验收文档，补充 `serve` 启动自检自动迁移、真实 UI/CLI 联调方式和 schema readiness 语义。
- 将真实云端 API 联调约束、PySide6 镜像源问题和真实云端 PostgreSQL 未迁移问题沉淀到 `AGENTS.MD`。
- 已验证 `client/` 下 `UV_LINK_MODE=copy uv sync` 成功。
- 已验证 `client/` 下 `UV_LINK_MODE=copy uv run python -m compileall src tests` 通过。
- 已验证 `client/` 下 `UV_LINK_MODE=copy uv run pytest` 通过，11 个测试通过。
- 已验证 `cloud-api/` 下 `go test ./...` 通过。
- 已验证仓库根目录 `git diff --check` 通过。
- 已验证仓库根目录 `.\go_build.ps1` 成功构建包含启动自检自动迁移能力的 `dist/cloud-api/linux-amd64/cloud-api`。

提交摘要：本次提交完成 PySide6 百度设置页、真实云端联调 CLI、客户端 UI 授权流程控制器和服务端启动自检自动迁移/readiness 检查；真实云端授权链路当前需部署并重启新二进制，让服务自动补齐 PostgreSQL schema 后继续。

后续待办：

- 将 `dist/cloud-api/linux-amd64/cloud-api` 部署到服务器，并用 `cloud-api serve --env-file /实际路径/.env` 或等效进程守护命令重启服务；启动阶段会自动执行 schema 自检和内置迁移。
- 重启后确认 `https://backup.baichengedu.com/v1/readyz` 返回 200，不再返回 `schema_not_ready`。
- 继续使用真实联调 CLI 或 PySide6 UI 跑设备码授权，完成百度官方页面授权、密文 token 入库和账号选择状态验证。
- 新二进制重启前不要继续用客户端授权流程判断 UI 或云端授权是否失败，因为当前线上失败根因是旧服务没有自动补齐 PostgreSQL schema。

### 真实云端百度授权入库与后续接口联调

- 真实服务器已重新部署并重启新版 Go 服务；启动日志确认 `.env` 已加载，PostgreSQL 连接到 `bdnetdesk_backup_db/bdnetdesk_backup_service`，启动自检发现缺表后自动执行内置 `001_cloud_sync.sql` 和 `002_baidu_auth.sql` 迁移并复查通过。
- 已验证真实 `https://backup.baichengedu.com/v1/healthz` 与 `/v1/readyz` 均返回 HTTP 200，readyz 不再仅代表数据库可 ping，而是包含关键 schema 可用性。
- 已在真实云端注册临时设备并验证 `/v1/devices/register` 不再因 PostgreSQL 缺表返回 500。
- 已验证真实设备码授权 session 可创建，返回百度官方授权地址、用户码和二维码地址；设备码授权在百度官方页面完成后，云端成功保存密文 token。
- 已确认真实云端账号列表中已有 1 个百度账号，`token_valid=true`、`token_version=1`、`last_verify_status=valid`；输出记录只保留脱敏 UID 和状态，不记录 Device Token、access token、refresh token、wrapping key 或密文 envelope 正文。
- 已基于该已入库授权继续测试真实云端后续接口：账号选择成功，密文 token 元数据可读取，刷新租约第一台设备获取成功，第二台设备并发获取返回 409 且 `acquired=false`，互斥行为符合预期。
- 发现 password 模式当前缺少持久化 KDF salt/参数：UI 完成授权时用随机 salt 派生 wrapping key，但云端密文 token envelope 不包含 `password_kdf.salt`，UI 退出后仅凭用户密码无法重新派生同一 key 解密刚才已入库 token。
- 已按用户要求在本机创建 `client/.env` 保存测试授权密码和真实云端 base URL，确认该文件被 `.gitignore` 忽略；该本地文件只用于后续联调，不纳入提交。
- 修复 PySide6 百度设置页后台任务生命周期：`QRunnable` 改为禁用自动删除，页面持有 worker 到 `finished` 后清理，并通过 `_api_lock` 串行化共用 `httpx.Client` 的真实 API 调用。
- 修复 UI busy 状态过粗问题：账号列表刷新只锁账号相关按钮，设备码创建和完成授权只锁对应按钮，避免账号刷新时阻塞“创建设备码”。
- 将 uv 用户目录权限、当前 Windows Go 工具链异常、PySide6 worker 生命周期问题沉淀到 `AGENTS.MD`。
- 已验证 `client/` 下 `UV_LINK_MODE=copy`、仓库内 `UV_CACHE_DIR` 执行 `uv sync` 成功。
- 已验证 `client/` 下 `UV_LINK_MODE=copy`、仓库内 `UV_CACHE_DIR` 执行 `uv run pytest` 通过，11 个测试通过。
- 已验证 `client/` 下 `UV_LINK_MODE=copy`、仓库内 `UV_CACHE_DIR` 执行 `uv run python -m compileall src tests` 通过。
- 已尝试恢复本地 Go 测试：当前 `go1.26.2` 环境连 `go list runtime` 都返回 `package runtime is not in std`；改用 `GOTOOLCHAIN=go1.25.0` 后标准库编译出现 compiler internal error，判断为本机 Go 工具链/环境问题，需换干净 Go 工具链后恢复 `cloud-api/` 下 `go test ./...`。

提交摘要：本次提交完成真实云端百度授权入库和后续云端接口联调，修复 PySide6 授权 UI 后台任务生命周期和按钮 busy 状态问题，并记录本机 uv/Go 工具链约束；已入库授权可继续用于账号选择、密文 token 元数据和刷新租约测试，但进入百度上传前必须补齐 password KDF 参数持久化以恢复 token 本地解密能力。

后续待办：

- 补齐 password 模式 KDF salt/参数持久化方案；优先使用 Windows DPAPI/凭据管理器保存敏感材料，避免仅依赖 `.env`。
- 对已入库真实账号重新完成一次带持久化 KDF 参数的新授权，或提供受控 re-encrypt 流程，让客户端重启后能用用户密码解密云端密文 token。
- 修复本机 Go 工具链或切换干净 Go 环境后，恢复 `cloud-api/` 下 `go test ./...` 常规验收。
- 在 token 可本地解密后，继续真实百度容量/用户信息、预上传、分片上传和 create 链路联调。
