# 开发排期与进度

本文件用于记录项目开发排期、当前进度和提交完成情况。每次开发开始前必须更新当前工作项、计划修改范围和验收标准；每个小功能完成后，必须先更新完成记录、提交摘要和后续待办，再将本文件与代码和文档一起纳入同一个 commit。

## 当前阶段

2026-06-07 进度审计结论：项目已经完成云端同步服务、真实云端部署联调、百度授权与账号选择、本机 Device Token/KDF 凭据、百度上传核心链路、本地 SQLite 上传账本、`uploadid` 断点续传、`.meta.json`/`job.index.json` 生成、`sync_outbox` worker、脱敏 `sync-outbox` CLI、百度 `list/listall` 客户端列表能力和 `upload-resumable -> sync-outbox -> 百度删除清理` 一键真实联调 CLI。当前仍处于 v1.3 底座和上传链路验证阶段，尚未进入完整桌面端备份产品可发布状态。

离完整完成开发计划的主要缺口：备份任务主 UI、扫描与内容指纹、完整内容去重、7-Zip AES-256 归档与加密 manifest、缓存额度和 artifact 生命周期、远端对象校对与人工修复、来源映射、原始数据清理、恢复流程、打包发布和端到端验收。按产品规格 v1.3 的阶段拆分估算，底座约完成 40%，可发布主流程仍约剩 60%；其中最大风险集中在 7-Zip/manifest/恢复正确性、校对修复安全边界、缓存压力控制和 PySide6 主流程体验。

## 当前工作项

- 已完成 P0 远端对象校对 worker 第一阶段：客户端本地只读校对模块和脱敏 CLI 报告，读取本地 `remote_objects`/`upload_sessions` 并调用百度 `list/listall` 生成差异清单。
- 本轮实现继续保持只读边界：不自动删除、覆盖、重传或写入校对结果；不修改 Go 服务端、不新增 PostgreSQL 迁移、不新增百度下载接口。
- 校对状态已覆盖 `consistent`、`db_exists_remote_missing`、`remote_meta_missing`、`remote_meta_mismatch`、`remote_size_mismatch`、`fs_id_changed`、`baidu_only` 和 `remote_unreadable`。
- 下一步回到 P0 远端对象校对与人工修复的后半段：基于只读报告设计人工确认/修复入口；真实 keep-remote 校对联调需在运行时凭据和授权密码可用时执行并清理远端对象。

## 本次验收标准

- 单元测试必须覆盖全部差异状态、分页、`listall` 限速、不可读目录、百度侧额外对象、缺失 `.meta.json`、size/md5/fs_id 不一致。
- CLI 测试必须覆盖 scope 参数互斥、脱敏输出、安全错误摘要，并确认不泄露 Device Token、百度 token、用户密码、SQLite 路径、本地路径、payload 明文或远端真实路径。
- 递归 `listall` 校对必须按产品规格默认每分钟不超过 8 次，并通过可注入 sleeper/rate limiter 测试。
- 客户端测试、`compileall` 和仓库根目录 `git diff --check` 必须通过；真实验证若使用 `integration_cli run-resumable --keep-remote` 保留远端对象，校对后必须再用 `cleanup-resumable` 清理。

## 开发排期

后续开发必须按下表推进。临时 fix 或新增需求只有在安全、数据一致性、真实联调阻塞或用户明确要求时可以插队；插队前必须在“排期变更记录”中写明原因、影响阶段、验收标准和回到主排期的条件。

| 优先级 | 阶段 | 工作内容 | 当前状态 | 下一步验收 |
| --- | --- | --- | --- | --- |
| P0 | 0-1 项目底座 | 仓库初始化、产品规格、Python 客户端骨架、配置加载、日志脱敏、uv 依赖 | 已完成 | 只做维护；入口 README 需保持与真实进度一致 |
| P0 | 2 本地/云端同步底座 | SQLite 迁移、版本字段、`sync_outbox`、Go Cloud Sync、PostgreSQL revision 投影、设备认证 | 基本完成 | 后续业务表接入时必须同事务写 outbox，并用真实云端 summary 校验 |
| P0 | 3 百度授权与上传底座 | OAuth/扫码授权、DPAPI Device Token、KDF store、token 解密、刷新租约、容量、预上传、分片、create、删除清理 | 基本完成 | 保持真实百度 API 验收；补上传失败重试 UI 和 token refresh 自动接入 |
| P0 | 4 远端对象校对与人工修复 | 百度 `list/listall`、本地 `remote_objects` 对账、差异状态、只读报告、人工修复入口 | 只读 worker/脱敏 CLI 已完成；人工修复 UI 未开始 | 后续基于报告设计人工确认与修复入口；任何删除、覆盖或重传必须用户确认并写版本记录 |
| P1 | 5 备份任务主 UI 与任务模型 | 任务页、拖拽/选择源、暂停继续取消、状态机、任务持久化 | 未开始 | PySide6 主窗口可创建任务并持久化，状态不遮挡、不泄密 |
| P1 | 6 扫描与内容指纹 | 递归扫描、不可读记录、快速指纹、完整 MD5/SHA256、文件夹 manifest hash | 未开始 | 单元测试覆盖路径无关指纹、symlink/junction 默认跳过、不可读不中断 |
| P1 | 7 去重索引与来源引用 | 本地内容对象、归档对象、来源映射、云端去重候选查询 | 未开始 | 最终去重只按完整 SHA256+size；路径/时间/设备不进入内容指纹 |
| P1 | 8 7-Zip 加密归档与 manifest | 明文 manifest 临时生成、7-Zip AES-256、archive 分包、标准/严格验证、验证后删除明文 manifest | 未开始 | 真实 7-Zip test、manifest hash 校验、明文 manifest 生命周期测试通过 |
| P1 | 9 端到端备份编排 | 扫描 -> 指纹 -> 去重 -> manifest/archive -> 验证 -> 百度可恢复上传 -> outbox 同步 -> 远端校对 | 底层上传链路已验证；主编排未开始 | 小文件、跨分片、重复内容、冲突、断点恢复均用真实链路验收 |
| P2 | 10 缓存额度与 artifact 管理 | 缓存目录、40GiB 规则、可释放统计、清理等级、artifact 生命周期 | 未开始 | 缓存不足时阻止新任务或暂停低优先级阶段，不删除源文件 |
| P2 | 11 来源映射和校对 UI | 来源与远端映射页、数据库与百度校对页、差异筛选、人工确认修复 | 未开始 | UI 能展示差异、允许本地/云端/百度实际状态人工处理，所有动作留版本记录 |
| P2 | 12 原始数据清理 | 手动触发、回收站优先、清理前源文件复查、清理记录同步 | 未开始 | 远端确认和本地记录完整前不可清理；源文件变化时按钮禁用 |
| P2 | 13 恢复流程 | 选择恢复对象、下载 archive、解密解压、按 manifest 恢复、SHA256 复验、冲突默认保留两者 | 未开始 | 原路径/手动路径恢复均可验收，覆盖缺失外部 archive 和密码错误 |
| P3 | 14 打包发布与最终验收 | PyInstaller/Nuitka、版本号、构建产物、发布文档、端到端验收矩阵 | 未开始 | 干净 Windows 环境完成安装、授权、备份、校对、清理、恢复和卸载/升级测试 |

## 进度差异审计

- README 入口文档曾停留在“Go 云端同步服务基础接口、部署构建脚本和百度授权管理接口”阶段，低估了实际进度；实际代码已具备真实百度授权、真实上传、断点续传、outbox 同步和清理联调能力。本轮同步更新 README 当前状态。
- 原排期把“PySide6 基础 UI、任务页、设置页”合并为一个阶段，容易误判 UI 接近完成；实际只完成百度设置页和授权相关 UI，备份任务页、缓存页、密码页、去重页、校对页、清理页、恢复页均未完成。
- 原排期把“百度 OAuth、预上传、分片上传、创建文件”标为部分完成；按近期提交和真实批测结果，该底座已基本完成，但主备份编排尚未把扫描、7-Zip 和 manifest 产物接入真实上传。
- 原排期未单独列出“进度审计、排期治理、临时 fix 记录规则”；本轮新增排期变更约束，后续任何插队需求都必须有文字说明。
- 产品规格要求的扫描、内容指纹、最终去重、7-Zip 加密 manifest、缓存策略、来源映射、清理和恢复尚无对应实现文件或测试，不能把当前上传链路联调成功等同于完整 v1.3 发布就绪。

## 排期变更记录

### 2026-06-07：进度审计与排期治理约束

变更原因：用户要求审计项目进度，确定离完整完成开发计划和发布还差多少，核对项目进度文件与实际进度差异，并要求后续开发遵循排期。

影响阶段：新增 P0 文档治理工作；重新拆分 P0-P3 后续阶段，把远端对象校对列为下一阶段，把主备份 UI、扫描、去重、7-Zip/manifest、缓存、清理、恢复和打包验收列为后续主线。

验收标准：`docs/roadmap_progress.md` 包含审计结论、差异说明和可执行排期；`AGENTS.MD` 包含后续必须遵循排期以及临时 fix/新增需求必须同步更新进度和排期的约束；README 当前状态不再停留在旧阶段。

回到主排期条件：本轮文档约束提交完成后，下一开发项回到 P0 远端对象校对 worker。

## 完成记录

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
