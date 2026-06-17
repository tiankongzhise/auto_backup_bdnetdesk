# P3-14 打包发布与最终验收矩阵

本文件记录 P3 阶段 14 的发布构建方式、官方依据和最终验收矩阵。发布候选必须在本矩阵全部通过后，才能标记为 v1.3 桌面端可交付。

## 发布构建

当前客户端发布包采用 PyInstaller onedir Windows GUI 方案，入口为 `auto_backup_client.app`，底层启动 pywebview 主窗口并加载打包内置的原生静态 `webui`。

从仓库根目录执行 dry-run：

```powershell
.\client_build.ps1 -DryRun
```

从仓库根目录执行真实构建：

```powershell
.\client_build.ps1
```

默认输出目录：

```text
dist/client/<yyyyMMdd-HHmmss>/AutoBackupBDNetdisk/
```

可通过 `-BuildId <批次名>` 固定批次目录；自定义 `-DistDir` 时也会在输出根目录下追加同一批次层，避免覆盖旧构建或混合不同批次产物。

构建脚本固定使用仓库内缓存目录，并按批次隔离 PyInstaller work/spec：

```text
.cache/uv
.cache/tmp
.cache/pyinstaller/<BuildId>
.cache/pyinstaller-spec/<BuildId>
```

这些目录和 `dist/` 均不得提交。

## 官方依据

PyInstaller 官方文档获取记录：

- 2026-06-08 已通过提升后的 `curl.exe -L https://pyinstaller.org/en/stable/usage.html` 获取官方 Usage 页面。
- 2026-06-08 已通过提升后的 `curl.exe -L https://pyinstaller.org/en/stable/runtime-information.html` 获取官方 Run-time Information 页面。

本轮实现依据：

- Usage 页面说明 PyInstaller 会生成 `dist` 中的 bundled app，并提供 `--distpath`、`--workpath`、`--clean`、`--noconfirm`、`--windowed`、`--add-data` 等构建选项。
- Run-time Information 页面说明打包应用启动时 PyInstaller bootloader 会设置 `sys._MEIPASS`，可用于定位 bundle 内数据文件。
- 本项目因此把 SQLite 迁移目录作为 `--add-data` 加入 bundle，并在运行时优先从 `sys._MEIPASS/migrations/sqlite` 定位迁移。

## 验收矩阵

| 编号 | 场景 | 环境 | 操作 | 通过标准 | 当前状态 |
| --- | --- | --- | --- | --- | --- |
| R01 | 发布构建 dry-run | 开发机 | `.\client_build.ps1 -DryRun` | 输出 PyInstaller 命令，包含 GUI onedir、源码路径、迁移 data 和带 `<BuildId>` 的仓库内输出/缓存目录 | 本轮已验收 |
| R02 | 发布构建 | 开发机 | `.\client_build.ps1` | 生成 `dist/client/<BuildId>/AutoBackupBDNetdisk/AutoBackupBDNetdisk.exe`，无敏感文件进入 dist | 本轮已验收开发机构建；干净机待验收 |
| R03 | 迁移定位 | 开发机/打包包 | 启动后初始化新 SQLite | 源码运行读取 `client/migrations/sqlite`；打包运行读取 bundle 内 `migrations/sqlite` | 本轮自动化验收源码与 `_MEIPASS` 分支；打包 exe 启动待干净机验收 |
| R04 | 首次启动 | 干净 Windows | 双击 exe | UI 启动，不要求已有 Device Token；能自动注册或提示真实云端配置问题 | 待执行 |
| R05 | 百度授权 | 干净 Windows | 设备码/扫码授权 | 只在百度官方页面输入百度账号；本机保存 DPAPI Device Token/KDF 材料；不落仓库路径 | 待执行 |
| R06 | 备份 | 干净 Windows | 主 UI 通过“添加来源”或拖拽添加单文件和文件夹，输入运行时归档密码/授权密码、确认缓存目录后点击开始 | 单一添加来源入口可混选文件/文件夹，后端自动识别；完成扫描、去重、按每个选择源生成独立 7-Zip 加密归档、百度上传、outbox 同步、远端校对和 completed final sync；运行时不闪现 CMD 窗口；云端导入的 `running`/暂停/等待重试任务可通过“继续”恢复执行；不能 completed 时任务显示最后阶段和原因；`job.index.json` 汇总本 job 全部 archive；UI 不保存或显示密码/token/完整路径 | 开发机自动化已覆盖统一添加来源、主 UI 调用 BackupPipeline、多来源归档、archive/upload/remote/completed 写入、校对差异收口和 running 任务继续按钮；干净机真实百度待执行 |
| R07 | 校对 | 干净 Windows | 来源映射、远端校对和云端同步页查看同一 job | 来源映射页刷新前拉取本机云端历史；任务筛选默认“全部最近记录”，按任务名/状态/更新时间选择，不要求普通用户输入 job_id；展示来源/远端关系和 consistent 状态；远端校对明确本地 SQLite `remote_objects/upload_sessions` 对比百度 `list/listall`；云端同步页可回读 Cloud Sync summary；不显示 Device Token、百度 token、密码或完整敏感路径 | 开发机自动化已覆盖来源映射脱敏、共享历史刷新、远端校对 UI 修复写入、长提示可读和云端同步页摘要回读；干净机待执行 |
| R08 | 清理 | 干净 Windows | 对已完成 job 执行回收站清理 | 原始数据清理页刷新前拉取本机云端历史；任务筛选默认“全部最近记录”，不要求用户输入 job_id；清理前复查通过后移入回收站，写入清理记录并同步 outbox；永久删除默认隐藏在高级选项，清理前必须选中候选 | 开发机自动化已覆盖候选门槛、共享历史刷新、身份复查、同步 payload 脱敏和 UI 高级删除/选择门槛；干净机待执行 |
| R09 | 恢复 | 干净 Windows | 从本地 archive 或远端 archive 按备份来源恢复文件和文件夹来源 | 候选每行代表备份时选择的一个文件或文件夹来源，不暴露文件夹内部单文件恢复入口；恢复时按 archive 整包下载/解压，但只写出所选来源范围；7-Zip 解密、manifest 校验、SHA256 复验通过；手动恢复文件夹来源保留根文件夹和内部目录结构；远端 archive 可经百度 dlink 拉取；冲突默认保留两者 | 开发机自动化已覆盖来源级候选聚合、本地恢复、UI 远端拉取恢复、文件夹结构恢复、SHA256 复验和路径脱敏；干净机待执行 |
| R10 | 断网补偿 | 干净 Windows | 云端临时不可用时执行本地任务并恢复网络 | 本地 SQLite 落盘；云端恢复后 outbox 可补偿同步 | 待执行 |
| R11 | 升级 | 干净 Windows | 用新包覆盖旧包目录后启动，或使用新 exe 搭配空本地 SQLite 启动备份/来源映射/清理/恢复页 | 本地 DPAPI 凭据和 SQLite 数据可继续使用；迁移幂等；若本机 Device Token 仍在，相关页面可按本机 `device_id` 从云端拉取本设备历史备份记录并重建任务列表、来源映射、清理候选和来源级恢复候选 | 开发机自动化已覆盖云端历史 payload 重建空 SQLite、共享历史刷新和恢复候选；干净机待执行 |
| R12 | 卸载/清理 | 干净 Windows | 删除程序目录并保留/清除用户数据 | 程序目录可移除；用户数据、凭据和缓存清理边界清晰 | 待执行 |
| R13 | 云同步真实性审计 | 开发机/干净 Windows | 运行 `auto_backup_client.cloud_sync_audit_cli` | 真实云端 `/v1/readyz` 为 ready；探针 revision 返回 `synced`；`GET /v1/reconcile/entities/{entity_id}` 回读 `revision_id`、`data_version`、`canonical_record_sha256` 匹配；重复提交同一 revision 返回 `duplicate` | 开发机已验收：`first_sync_status=synced`、`summary_matched=true`、`duplicate_sync_status=duplicate`、`duplicate_verified=true`、`cloud_sync_truthful=true`；干净机待复验 |
| R14 | 敏感信息审计 | 开发机/干净 Windows | 运行 `auto_backup_client.release_sensitive_audit` 检查 dist、日志、SQLite outbox 和 UI 输出 | 无 `.env`、token、密码、wrapping key、明文 manifest、SQLite 数据库文件或完整敏感路径泄漏；CLI 发现泄漏时只输出脱敏 finding | 开发机自动化入口已补齐，干净机真实 dist/log/SQLite/UI 输出待执行 |

## 发布阻塞项

- 尚未执行干净 Windows 真实安装/授权/备份/校对/清理/恢复/升级/卸载矩阵。
- 尚未生成最终安装器；当前是 onedir 发行目录，后续可在同一矩阵基础上接入安装器。
- 覆盖恢复仍未开放；如发布前要求覆盖恢复，必须先实现覆盖前回收站保护并新增验收项。
- 云同步真实性审计不得只看本地 `sync_outbox` 状态；必须保留真实 Cloud Sync API 写入和云端 summary 回读匹配证据，确认不是虚假同步。
- R14 自动化审计只能证明指定输入未泄漏；干净机验收时仍必须把实际发布目录、运行日志、UI 导出文本和真实本地 SQLite 作为输入执行。

## R14 敏感信息审计命令

开发机或干净 Windows 生成发布目录后，在 `client/` 下执行：

```powershell
$env:UV_LINK_MODE='copy'
$env:UV_CACHE_DIR='..\.cache\uv'
uv run python -m auto_backup_client.release_sensitive_audit --scan-path ..\dist\client\<BuildId>\AutoBackupBDNetdisk --sqlite-path ..\var\data\backup_state.sqlite3
```

如需同时检查日志或 UI 导出文本，可重复传入 `--scan-path`：

```powershell
uv run python -m auto_backup_client.release_sensitive_audit --scan-path ..\dist\client\<BuildId>\AutoBackupBDNetdisk --scan-path <日志目录> --scan-path <UI导出文本> --sqlite-path <本地SQLite>
```

通过标准：命令返回 0，输出 `release_sensitive_audit_passed: true`。若发现 `.env`、SQLite 数据库文件、明文 manifest、`Authorization: Bearer ...`、`bdn_...` Device Token、百度 access/refresh token、授权/归档密码、wrapping key、private key、完整 Windows 本地路径或敏感 outbox 字段，命令必须返回非 0，且 finding 输出不得回显敏感原文。

## P3-14 发布候选体验与恢复同步修复记录

2026-06-15 修复发布候选体验、来源级恢复和设备历史拉取，开发机自动化/静态验证结果：

- 7-Zip 归档、测试、解压和发布构建 subprocess 统一使用 Windows 隐藏窗口参数，定向测试覆盖工具函数和 7-Zip runner 调用层。
- 备份任务页合并为统一“添加来源”入口，拖拽和后端 `Path.is_dir()/Path.is_file()` 自动识别文件/文件夹来源。
- 备份流水线上传、Cloud Sync 后如果远端校对存在差异，不再静默保持 `running`，会写入 `failed_retryable`、最后阶段和差异原因。
- 恢复候选改为来源级聚合；文件夹来源恢复时整包解压 archive，并按 manifest 只恢复所选来源范围。
- 云端新增 `GET /v1/backups?device_id=current` 设备级历史接口，客户端恢复页刷新时使用本机 Device Token 幂等拉取本设备历史记录，可从空 SQLite 重建来源级恢复候选。
- 已验证客户端定向 42 项通过、恢复页 UI 定向 3 项通过、`python -m compileall src tests` 通过、Go `go test -p=1 ./...` 通过；干净 Windows 真实 R04-R14 仍需执行。

2026-06-15 继续修复发布候选任务入口与历史同步体验：

- `添加来源` 改为单一自定义选择器，可混选文件和文件夹，后端继续统一自动识别来源类型。
- 备份任务页刷新时会拉取本机云端历史；云端导入的 `running`、`paused`、`failed_retryable` 任务在本机无后台 worker 时可通过“继续”执行。
- 来源映射、原始数据清理和恢复页共用设备历史刷新 helper，任务筛选改为“全部最近记录”+ 任务下拉，不要求普通用户输入内部 `backup_job_id`。
- 已验证定向 UI/历史同步 pytest 20 项通过；后续仍需补跑全量客户端测试、compileall、Go 测试和干净 Windows 真实 R04-R14。

## P3-14 全量审计补测记录

2026-06-12 修复授权隔离、备份粒度、恢复结构和校对可视化，开发机自动化/静态验证结果：

- 云端百度授权改为按 `account_id + device_id` 保存密文 token 和刷新租约，同一百度 UID 在不同设备授权不会覆盖其他设备授权；客户端 KDF store 兼容 account+device 记录和旧 account 级记录。
- 备份流水线按每个选择源独立归档上传，`job.index.json` 汇总本 job 全部 archive；跨来源重复内容在 manifest 和 `archive_members` 中保留引用。
- 备份任务页新增缓存目录输入和选择按钮，完成提示展示归档数量与上传分片总数；恢复文件夹来源到手动目录时保留根文件夹和内部结构。
- 原始数据清理 UI 对错误确认词提示 `确认短语应为 CLEANUP_SOURCES`；远端校对表格支持换行、tooltip 和明确 SQLite/Baidu 校对口径；新增云端同步页用于本地 outbox/sync_status 与 Cloud Sync summary 回读。
- 已验证 Go `go test ./...` 通过；Python 定向 48 项通过；`python -m compileall client/src client/tests` 和 `git diff --check` 通过。修复后全量 `uv run pytest` 因本地提升审批器 429 未能再次执行，需在审批恢复或干净机验收时补跑。

2026-06-09 修复主 UI 真实备份闭环缺口，开发机自动化结果：

- 备份任务页“开始”按钮改为后台调用 `BackupPipeline`，使用当前设备凭据和当前选中百度账号，执行扫描、去重、7-Zip 归档、百度上传、Cloud Sync、远端校对和 completed final sync。
- 备份页新增运行时归档密码和授权密码输入；授权密码留空时复用归档密码；启动任务后立即清空输入框，状态栏只展示文件数、归档序号、上传分片数和阶段结果。
- 自动化测试使用 fake Cloud/Baidu 客户端验证 UI 开始按钮会把 job 跑到 `completed`，并写入 `archives`、`upload_sessions`、`remote_objects`，同时状态消息不包含本地完整路径或密码。
- 干净 Windows 仍需用真实百度账号执行 R04-R14，确认真实远端文件保留、来源映射、远端校对、回收站清理和恢复闭环。

2026-06-08 发布交付审计中补齐 UI 远端恢复拉取闭环和清理 UI 安全门槛，开发机自动化结果：

- `tests/test_backup_task_page.py tests/test_restore_flow.py tests/test_source_cleanup.py` 共 17 项通过。
- 恢复页在本地 archive 缺失但 `remote_objects` archive 为 `remote_created` 且有 `fs_id` 时，会要求授权密码，经 Cloud API 读取密文 token、本机解密后使用百度 `filemetas`/`dlink` 下载 archive，再做 archive SHA256、7-Zip、manifest 和恢复后 SHA256 校验。
- 原始数据清理页默认只提供回收站和隔离目录；永久删除在高级选项打开前隐藏，且执行清理必须选中候选行。
- 本轮未删除既有百度备份结果，未执行真实远端清理动作；真实远端删除仍只允许在用户显式确认的测试清理或人工操作中执行。

## R13 开发机审计记录

2026-06-08 使用本机 DPAPI Device Token 对真实 `https://backup.baichengedu.com` 执行 `uv run python -m auto_backup_client.cloud_sync_audit_cli`，结果：

- `probe_entity_id_sha256=3983c32795e88a23d32bbb0a9e9e7514eeec4093bbfec641716e511fa459a2e6`
- `probe_event_id_sha256=39ce68fa416500ec7ef0aba93171d7e3e7024d9dad5fae67818b7549f8dc3b7c`
- `probe_revision_id=019ea787-7b7c-729e-bf7c-d9af48cd4707`
- `probe_record_sha256=8f6afc61936c27f27ecc30b662eaf2c72786cde4e3272a20dc2a49e67e2008c5`
- 首次同步返回 `synced`，云端 summary 回读匹配，重复提交返回 `duplicate`。
