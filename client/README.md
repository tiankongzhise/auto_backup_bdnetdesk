# Python 客户端

客户端源码、测试、迁移和客户端专用说明集中放在本目录，避免把客户端实现散布到仓库根目录。

## 当前范围

- `src/auto_backup_client/settings.py`：客户端运行配置读取。
- `src/auto_backup_client/redaction.py`：敏感字段脱敏工具。
- `src/auto_backup_client/device_credentials.py`：当前设备 Device Token 凭据存储，Windows 默认使用 DPAPI 保护。
- `src/auto_backup_client/baidu/cloud_api.py`：Go 云端百度授权 API 客户端。
- `src/auto_backup_client/baidu/auth_workflow.py`：百度授权 UI/CLI 复用的真实授权流程控制器。
- `src/auto_backup_client/baidu/real_auth_cli.py`：真实云端 API 联调命令行入口。
- `src/auto_backup_client/baidu/crypto.py`：百度密文 token envelope 的本地加解密。
- `src/auto_backup_client/baidu/kdf_store.py`：password 模式 account 级 KDF 参数持久化，Windows 默认使用 DPAPI 保护。
- `src/auto_backup_client/baidu/refresh.py`：refresh token 租约、百度 token 刷新和云端版本回写流程。
- `src/auto_backup_client/sqlite_store.py`：客户端 SQLite 迁移、事务和 `sync_outbox` 同事务写入基础设施。
- `src/auto_backup_client/baidu/metadata.py`：百度远端 `.meta.json` 和 `job.index.json` 非敏感稳定 JSON 生成工具。
- `src/auto_backup_client/baidu/resumable_upload.py`：基于 SQLite 上传账本的 `uploadid` 断点续传编排。
- `src/auto_backup_client/sync_cli.py`：脱敏 `sync-outbox` CLI，把本地 SQLite outbox 推送到真实云端 Cloud Sync API。
- `src/auto_backup_client/sync_worker.py`：读取本地 `sync_outbox`、调用云端 revision API 并回写同步状态的 worker。
- `src/auto_backup_client/backup_jobs.py`：备份任务模型服务层，负责创建任务、持久化来源和任务状态机版本化更新。
- `src/auto_backup_client/scan_fingerprints.py`：扫描与内容指纹服务，负责递归扫描、默认跳过链接/快捷方式、记录不可读问题、计算 quick fingerprint、完整 MD5/SHA256、`content_id` 和文件夹 hash。
- `src/auto_backup_client/dedupe_index.py`：内容级去重索引服务，负责把 `file_items` 汇总成 `content_objects` 和 `content_references`，并可查询云端内容候选。
- `src/auto_backup_client/archive_packager.py`：7-Zip 加密归档与 manifest 服务，负责生成临时明文 manifest、staging payload、AES-256 7z archive、标准验证和本地归档索引。
- `src/auto_backup_client/local_fs.py`：Windows 长路径安全本地文件访问工具。
- `src/auto_backup_client/cache_artifacts.py`：缓存 artifact 登记、预算检查、缓存等级和清理服务。
- `src/auto_backup_client/cache_artifacts_cli.py`：缓存状态和清理 CLI，输出只包含大小、状态和路径 hash。
- `src/auto_backup_client/source_mapping.py`：来源映射只读聚合服务，面向 UI 展示 job/source/file/content/archive/remote object 关系。
- `src/auto_backup_client/source_cleanup.py`：原始数据清理候选、清理前源文件身份复查、回收站/隔离目录/高级永久删除执行和版本化清理记录。
- `src/auto_backup_client/restore_flow.py`：恢复候选、archive 复用/下载边界、7-Zip 解密解压、manifest 恢复、SHA256 复验、冲突保留两者和版本化恢复记录。
- `src/auto_backup_client/restore_cli.py`：脱敏恢复 CLI，可列出候选，执行本地 archive 恢复，并可显式启用百度 dlink 远端拉取。
- `src/auto_backup_client/backup_pipeline.py`：端到端备份编排服务，负责串联扫描、去重、归档、可选真实百度上传、可选 outbox 同步和可选远端校对。
- `src/auto_backup_client/backup_pipeline_cli.py`：端到端编排 CLI，默认只执行本地闭环，显式传入上传/同步/校对开关后才接入真实云端和真实百度链路。
- `src/auto_backup_client/real_backup_pipeline_test_cli.py`：真实百度上传全链路测试入口，生成临时源文件后跑完整主流程、校验云端 summary、执行同路径冲突探针并清理本批远端对象。
- `src/auto_backup_client/cloud_sync_audit_cli.py`：真实 Cloud Sync 同步真实性审计探针，直接提交无敏感临时 revision 并回读云端 summary，确认不是仅本地假同步。
- `src/auto_backup_client/ui/main_window.py`：PySide6 主窗口、备份任务页、来源映射页、远端校对页、原始数据清理页和恢复页；写入类动作默认脱敏展示。
- `src/auto_backup_client/ui/baidu_settings.py`：PySide6 百度设置页，展示账号列表、设备码授权、二维码和授权完成反馈。

## PySide6 主窗口

主窗口提供备份任务、百度设置、来源映射、远端校对、原始数据清理和恢复入口。备份任务页支持选择/拖拽来源、创建任务、开始、暂停、继续和取消；端到端执行仍由 `backup_pipeline_cli` 和后续任务编排入口承载。来源映射页展示本地 SQLite 中 job、source、file/content/archive/member/remote object 的关联，默认使用路径 hash、文件名和状态字段展示关系。远端校对页可按 job、upload session 或 remote dir 调用真实百度列表接口生成差异报告，默认 dry-run；只有填写确认短语后才把可审计修复写入 `remote_objects` 和 `sync_outbox`。原始数据清理页只列出已完成、已验证、远端对象已确认且源文件未变化的候选，默认移入 Windows 回收站；永久删除默认隐藏在高级选项中，且必须额外填写确认短语。恢复页可按 job/关键字筛选候选，选择手动目录或原路径，运行时输入归档密码后执行 7-Zip 解压恢复；本地 archive 缺失但远端 archive 已确认时，恢复页会要求授权密码并通过真实百度 dlink 拉取 archive 后再校验恢复。

```powershell
cd client
$env:UV_LINK_MODE='copy'
$env:UV_CACHE_DIR='..\.cache\uv'
$env:LOCAL_SQLITE_PATH='..\var\data\backup_state.sqlite3'
$env:CLOUD_API_BASE_URL='https://backup.baichengedu.com'
uv run python -m auto_backup_client.ui.main_window
```

`backup_jobs` 是版本化同步实体，创建任务和状态变更会同事务写入 `sync_outbox`。`backup_sources.local_path` 当前只保存在本地 SQLite，`sync_outbox.payload_json` 不包含本地来源路径；任务页状态栏和任务列表也只展示任务名、状态、来源数、同步状态、版本和更新时间。来源映射和远端校对 UI 不把本地 SQLite 路径、Device Token、百度 token、用户密码或 wrapping key 输出到界面日志。

## 发布构建

P3-14 发布骨架使用 PyInstaller onedir Windows GUI 方案。仓库根目录提供 `client_build.ps1`，会通过 uv 调用 `auto_backup_client.release_build`，并把构建缓存固定到仓库内 `.cache/`。

```powershell
cd ..
.\client_build.ps1 -DryRun
.\client_build.ps1
```

如果当前 PowerShell 执行策略禁止直接运行本地脚本，可使用：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\client_build.ps1 -DryRun
powershell -NoProfile -ExecutionPolicy Bypass -File .\client_build.ps1
```

默认输出目录为 `dist/client/AutoBackupBDNetdisk/`。PyInstaller 参数由 `src/auto_backup_client/release_build.py` 生成，包含：

- `--onedir`
- `--windowed`
- `--distpath <repo>/dist/client`
- `--workpath <repo>/.cache/pyinstaller`
- `--specpath <repo>/.cache/pyinstaller-spec`
- `--paths <repo>/client/src`
- `--add-data <repo>/client/migrations/sqlite;migrations/sqlite`

SQLite 迁移目录定位顺序为：

1. `AUTO_BACKUP_SQLITE_MIGRATIONS_DIR`，仅用于测试或排障。
2. PyInstaller `_MEIPASS/migrations/sqlite`。
3. 源码树 `client/migrations/sqlite`。

PyInstaller 官方依据已记录在 `docs/release_acceptance_matrix.md`。当前发布包仍需完成干净 Windows 安装、授权、备份、校对、清理、恢复、升级/卸载和敏感信息审计矩阵后，才能作为 v1.3 可交付发布。

## 扫描与内容指纹

阶段 P1-6 已新增扫描服务和 SQLite `file_items`、`folder_items`、`scan_issues` 表。扫描服务会读取 `backup_sources.local_path`，默认递归普通目录，跳过 symlink、junction 和 `.lnk` 快捷方式；遇到不可读文件或目录时写入 `scan_issues`，不会中断同一任务继续扫描其他来源或文件。

内容指纹只描述文件字节内容，不包含路径、文件名、时间、设备或任务信息。完整文件身份为 `md5(full_bytes)`、`sha256(full_bytes)` 和 `content_id = sha256("v1:file:" + size + ":" + file_sha256)`；快速指纹只用于后续候选筛选，不作为最终去重依据。`file_items` 同时记录 Windows volume serial 和 file index，供断点复用和原始数据清理前复查。`file_items.local_path`、`folder_items.local_path` 和 `scan_issues.local_path` 只保存在本地 SQLite，`sync_outbox.payload_json` 会过滤本地路径。

阶段 P1-7 已新增内容级去重索引。`content_objects` 是版本化同步实体，会同事务写入 `sync_outbox`，payload 包含 `content_id`、`file_sha256` 和 `size_bytes`，用于云端跨设备去重索引；`content_references` 记录每个来源文件的引用、清理状态和恢复状态，本地绝对路径只保存在 SQLite，不进入同步 payload。

最终重复判断只使用完整 `sha256 + size_bytes`，并校验 `content_id = sha256("v1:file:" + size + ":" + file_sha256)`。同一内容的首个本地引用标记为 `needs_payload`，后续本地引用标记为 `local_duplicate`；如果云端 `GET /v1/contents/{content_id}` 返回的 `file_sha256` 和 `size_bytes` 与本地一致，引用才可标记为 `cloud_duplicate_candidate`。404、临时错误或 sha256/size 不一致都不能作为跳过 payload 的依据。

本阶段不执行 7-Zip 归档、manifest 文件生成、archive_objects 归档索引、百度上传、缓存清理或恢复；后续 P1-8 到 P1-9 会把 `content_references` 接入 manifest/archive 和端到端编排。

## 7-Zip 加密归档与 manifest

阶段 P1-8 已新增 `archives` 和 `archive_members` 表，以及 `ArchivePackager` 本地归档服务。服务会读取 `content_references`、`file_items` 和 `folder_items`，生成稳定 manifest JSON，并把需要 payload 的内容写入 archive 内部 `payload/{content_id}`；本地重复或云端候选引用不会重复写 payload，但会在 manifest 和 `archive_members` 中保留引用。没有新增 payload 的 job 也会生成 manifest-only archive。

7-Zip 可执行文件优先使用 `AUTO_BACKUP_7ZIP_PATH`，其次查找 PATH 中的 `7z`，再尝试 `C:\Program Files\7-Zip\7z.exe` 和 `C:\Program Files (x86)\7-Zip\7z.exe`。归档命令使用 7z 格式、LZMA2、密码参数和文件名加密参数；本机阶段测试使用真实 7-Zip 26.01 和密码 `Test123456789` 执行创建、`7z t` 标准验证、解出 `manifest/manifest.json` 并比对 manifest SHA256。

明文 manifest 只写入 job 缓存的 `manifest_plain/` 和压缩 staging 目录；标准验证完成后会删除 `manifest_plain/`、staging payload 目录和 verify 解压目录。`archives` 作为同步实体写入 `sync_outbox`，同步 payload 不包含本地 archive 路径、明文 manifest 路径、payload staging 路径或用户密码。P2-10 起，归档阶段会把 `manifest_plain`、staging、verify 和最终 archive 登记为本地-only cache artifact，供后续预算统计和安全清理使用。

## 端到端备份编排

阶段 P1-9 新增 `BackupPipeline`，把已完成的任务模型、扫描、内容去重、7-Zip 加密归档、可恢复上传、outbox 同步和远端校对串成单个 job 的最小主流程。默认本地模式只执行扫描、去重和归档，不读取 Device Token，不解密百度 token，也不会把 job 标记为 completed；只有显式启用真实上传并完成远端校对一致后，编排器才允许把任务更新为 completed。

本地闭环示例：

```powershell
cd client
$env:UV_LINK_MODE='copy'
$env:UV_CACHE_DIR='..\.cache\uv'
uv run python -m auto_backup_client.backup_pipeline_cli --source .\path\to\file --cache-root ..\var\cache --no-complete
```

真实链路示例：

```powershell
cd client
$env:UV_LINK_MODE='copy'
$env:UV_CACHE_DIR='..\.cache\uv'
$env:BAIDU_AUTH_PASSWORD='<runtime-only-authorization-password>'
uv run python -m auto_backup_client.backup_pipeline_cli --password-env BAIDU_AUTH_PASSWORD --source .\path\to\file --cache-root ..\var\cache --upload --sync-outbox --reconcile-remote
```

真实模式会复用 `BaiduResumableUploader` 上传 archive、`.meta.json` 和 `job.index.json`，并把上传账本中的 `archive_id` 对齐到归档阶段生成的 `archives.archive_id`。`mark_completed=True` 时，编排器会在远端校对一致后写入 job `completed`，并追加一次 final sync，把完成状态 revision 推送到真实云端。P2-10 起，CLI 默认执行 40GiB 有效缓存预算检查；测试或排障时可显式传 `--skip-cache-budget-check`。CLI 输出只展示 job、计数、hash、fs_id、缓存等级和远端路径 hash；不得输出 Device Token、百度 token、用户密码、wrapping key、本地来源路径、SQLite 路径、缓存路径或 manifest 明文。

## 缓存 Artifact 管理

阶段 P2-10 新增本地-only `cache_artifacts` 表和 `CacheArtifactManager`。缓存 artifact 包含 archive、manifest_plain、staging/tmp、verify、上传临时 JSON、download 和 restore 等本地文件或目录；路径只保存在本地 SQLite，不进入 `sync_outbox` 和云端 revision。清理入口只会删除已登记且位于 cache root 内的 artifact，不会删除用户源文件、未上传 archive、未上传 `.meta.json` 的 archive 或断点续传 SQLite 状态。

缓存状态和 dry-run 清理示例：

```powershell
cd client
$env:UV_LINK_MODE='copy'
$env:UV_CACHE_DIR='..\.cache\uv'
uv run python -m auto_backup_client.cache_artifacts_cli --cache-root ..\var\cache status
uv run python -m auto_backup_client.cache_artifacts_cli --cache-root ..\var\cache cleanup --stage completed
uv run python -m auto_backup_client.cache_artifacts_cli --cache-root ..\var\cache cleanup --stage completed --apply
```

缓存等级按产品规格分为 `sufficient`、`medium`、`tight` 和 `critical`。`BackupPipeline` 在新任务扫描、哈希、压缩前检查有效预算；低于 40GiB 或进入 critical 状态时会拒绝启动新任务。完成后可传 `--cleanup-cache-artifacts` 执行安全清理，或配合 `--cleanup-cache-dry-run` 只输出清理计划。CLI 只输出大小、数量、等级和路径 SHA256，不输出真实缓存路径。

## 原始数据清理

阶段 P2-12 新增 `source_cleanup_records` 同步实体和原始数据清理服务。清理只由用户手动触发，候选必须同时满足：job 已 `completed`、archive 标准验证通过、上传状态为 `remote_created`、`.meta.json` 与 `job.index.json` 已上传、本地 `remote_objects` 三类对象均为 `remote_created`。若 job 或清理记录仍待云端同步，UI 会显示 `sync_pending` 提示，但只要本地记录完整仍允许清理。

清理执行前会复查源文件 `size`、`mtime_ns`、Windows volume serial 和 file index；任何不一致都会禁止移动/删除并写入 failed 清理记录。默认方式是 Windows 回收站，底层使用 `SHFileOperationW` 并设置 `FOF_ALLOWUNDO`；隔离目录会把文件移动到用户指定目录下的 job 子目录；永久删除是高级选项，UI 默认隐藏，执行时必须同时填写 `CLEANUP_SOURCES` 和 `PERMANENT_DELETE_SOURCES`。UI 执行清理前必须选中候选行，避免空选择时误清理全部候选。

Windows API 实现依据来自 Microsoft Learn：[`SHFileOperationW`](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shfileoperationw) 用于文件操作，`FOF_ALLOWUNDO` 使删除进入回收站；[`GetFileInformationByHandle`](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getfileinformationbyhandle) / [`BY_HANDLE_FILE_INFORMATION`](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/ns-fileapi-by_handle_file_information) 提供 volume serial 和 file index。

每次实际执行都会写入 `source_cleanup_records`，并同事务写入 `sync_outbox`；同步 payload 不包含完整原始路径或隔离目录路径，只保留路径 SHA256、文件名、清理状态、方法、操作者、清理前大小/SHA256/mtime 和文件身份摘要。`content_references.cleanup_status` 会更新为 `cleaned` 或 `cleanup_failed`，来源映射页可继续展示清理状态。

真实全链路测试入口：

```powershell
cd client
$env:UV_LINK_MODE='copy'
$env:UV_CACHE_DIR='..\.cache\uv'
$env:TMP='..\.cache\tmp'
$env:TEMP='..\.cache\tmp'

# 可直接复用本目录被 git 忽略的 .env 中 CLOUD_API_BASE_URL 和 BAIDU_AUTH_PASSWORD；
# 如需临时覆盖，也可在当前 PowerShell 会话设置：
$env:CLOUD_API_BASE_URL='https://backup.baichengedu.com'
$env:BAIDU_AUTH_PASSWORD='<runtime-only-authorization-password>'

uv run python -m auto_backup_client.real_backup_pipeline_test_cli --password-env BAIDU_AUTH_PASSWORD
```

`real_backup_pipeline_test_cli` 默认使用仓库内 `.cache/real-pipeline/` 的临时 SQLite、缓存和源文件目录，不污染常规 `var/data`；会生成小文件和跨 4 MiB 分片源文件，执行 `scan -> dedupe -> 7-Zip archive -> quota -> precreate/resume -> locateupload -> superfile2 -> create -> .meta.json -> job.index.json -> sync-outbox -> cloud-summary -> baidu listall reconcile -> completed -> final sync -> conflict probe -> filemanager/delete cleanup`。验收时要求跨分片 archive 上传分片数大于 1、远端 3 个对象全部 `consistent`、completed job 云端 summary 匹配、同路径 `rtype=0` 冲突探针命中、清理 `errno=0`。如加 `--keep-remote` 保留远端对象，必须用输出的 `job_id` 和本次临时 SQLite 另行清理。

## 恢复流程

阶段 P2-13 新增 `restore_records` 同步实体和 `RestoreService`。恢复候选来自 `content_references`、`archives` 和 `remote_objects`，只要求备份 job 已 `completed`、archive 标准验证通过且来源已有 archive assignment；即使原始源文件已经清理，只要本地 archive 或已确认远端 archive 可用，仍会被列为可恢复候选。

恢复优先复用 `archives.local_archive_path` 指向的本地 archive。若本地 archive 缺失且 `remote_objects` 记录 archive 为 `remote_created` 并带有 `fs_id`，候选会进入 `needs_download`；恢复服务支持注入 `BaiduArchiveDownloader` 下载 archive。百度下载官方依据来自百度网盘开放平台文档：`filemetas` 通过 `GET /rest/2.0/xpan/multimedia?method=filemetas&fsids=[...]&dlink=1` 获取 `dlink`，下载时在 `dlink` 后追加 `access_token` 并设置 `User-Agent: pan.baidu.com`，`dlink` 有效期 8 小时且可能 302 跳转。

恢复执行会先校验 archive SHA256，再用真实 7-Zip 执行 `t` 和完整解压到 `{cache_root}/jobs/{job}/restore/` 临时目录，读取 `manifest/manifest.json` 并比对 manifest SHA256。恢复文件复制到目标后会重新计算 SHA256，与 manifest/content index 比对一致才写入 `restored`。密码错误、manifest 缺失、archive SHA256 不匹配、外部 payload archive 缺失或复制后 hash 不一致都会写入 failed 恢复记录，不会标记为恢复成功。

目标支持 `manual_path` 和 `original_path`。手动路径会按 manifest 的 `relative_path` 放到用户指定目录下；原路径使用 manifest/local reference 记录的原始路径。冲突策略默认 `keep_both`，生成 `restored yyyyMMdd-HHmmss` 后缀文件名，不覆盖现有文件；`skip_existing` 可跳过已有文件。覆盖恢复仍留待后续需要回收站保护时单独实现。

恢复 CLI 示例：

```powershell
cd client
$env:UV_LINK_MODE='copy'
$env:UV_CACHE_DIR='..\.cache\uv'
$env:BAIDU_AUTH_PASSWORD='<runtime-only-archive-password>'
uv run python -m auto_backup_client.restore_cli --cache-root ..\var\cache --job-id <job_id> list
uv run python -m auto_backup_client.restore_cli --cache-root ..\var\cache --job-id <job_id> --password-env BAIDU_AUTH_PASSWORD restore --target-root .\restored

# 本地 archive 已清理但远端 archive 已确认时，显式启用远端拉取。
uv run python -m auto_backup_client.restore_cli --cache-root ..\var\cache --job-id <job_id> --password-env BAIDU_AUTH_PASSWORD --enable-remote-download --authorization-password-env BAIDU_AUTH_PASSWORD restore --target-root .\restored
```

`restore_cli` 和恢复页输出只展示数量、状态、文件名、content/archive hash 和路径 hash，不输出完整本地来源路径、目标路径、SQLite 路径、缓存路径、归档密码或百度 token。每个实际结果都会写入 `restore_records`，并同事务更新 `content_references.restore_status`；同步 payload 会过滤 `target_path`、`final_path` 和 `archive_path`。

## PySide6 百度设置页

运行前通过运行时环境提供真实云端配置：

```powershell
cd client
$env:UV_LINK_MODE='copy'
$env:CLOUD_API_BASE_URL='https://backup.baichengedu.com'
uv run python -m auto_backup_client.ui.baidu_settings
```

页面启动时如果没有 `CLOUD_API_DEVICE_TOKEN`，会自动注册当前设备，并把 Device Token 保存到本机 DPAPI 凭据文件；也可通过运行时环境临时提供 `CLOUD_API_DEVICE_TOKEN`。页面会直接调用真实云端 API 读取账号、选择账号、生成扫码授权、轮询状态并自动完成密文 token 写入。完成授权后，客户端会按 `account_id` 保存 password KDF salt/参数；后续可选中账号并点击“验证解密”，用同一授权密码重新派生 wrapping key 并验证云端密文 token 可本地解密。

## 真实联调 CLI

检查真实云端：

```powershell
cd client
$env:UV_LINK_MODE='copy'
uv run python -m auto_backup_client.baidu.real_auth_cli health
```

读取账号或启动扫码授权时，CLI 会优先使用运行时 `CLOUD_API_DEVICE_TOKEN`，否则复用本机 DPAPI Device Token 凭据；如本机尚无凭据，会自动注册当前设备并保存。

```powershell
uv run python -m auto_backup_client.baidu.real_auth_cli accounts
uv run python -m auto_backup_client.baidu.real_auth_cli device-code --password-env BAIDU_AUTH_PASSWORD
uv run python -m auto_backup_client.baidu.real_auth_cli token-check --password-env BAIDU_AUTH_PASSWORD
```

如果需要一次性临时设备，可使用 `--register-ephemeral-device` 在真实云端临时注册设备，token 只在当前进程内使用，不会写入文件。

## 百度上传真实联调 CLI

上传入口会先通过真实云端解密当前选中账号的百度 token，再调用真实百度网盘 API。授权密码只通过运行时输入或指定环境变量提供，不写入仓库文件。

```powershell
cd client
$env:UV_LINK_MODE='copy'
$env:BAIDU_AUTH_PASSWORD='<runtime-only-authorization-password>'
uv run python -m auto_backup_client.baidu.upload_cli --password-env BAIDU_AUTH_PASSWORD quota
uv run python -m auto_backup_client.baidu.upload_cli --password-env BAIDU_AUTH_PASSWORD upload-file .\path\to\archive.7z --check-quota
uv run python -m auto_backup_client.baidu.upload_cli --password-env BAIDU_AUTH_PASSWORD upload-resumable .\path\to\archive.7z --check-quota
uv run python -m auto_backup_client.baidu.upload_cli --password-env BAIDU_AUTH_PASSWORD real-batch
```

`real-batch` 会生成临时小文件和跨 4 MiB 分片文件，执行容量检查、上传、同路径冲突检测，并默认使用百度文件管理删除接口清理本批远端测试文件。`uinfo` 只保留为独立排查命令；当前真实授权应用或路径调用官方 `iotqueryuinfo` 接口可能返回 HTTP 404，不作为上传批测前置条件。

`upload-resumable` 会按 `LOCAL_SQLITE_PATH` 初始化本地 SQLite，写入 `upload_sessions`、`upload_parts`、`remote_objects` 和 `sync_outbox`，并真实上传 archive、`.meta.json`、`job.index.json`。输出只包含账号 ID、token 版本、对象哈希、计数和 fs_id，不输出 access token、refresh token、wrapping key、Device Token 或本地敏感路径。

## upload-resumable 一键联调 CLI

底层上传账本排障可使用 `integration_cli run-resumable`，它会生成临时 archive，并在同一入口完成真实百度上传、本地 SQLite outbox 写入、真实 Cloud Sync 推送、云端 revision 摘要校验和百度 `filemanager/delete` 清理。默认会检查百度容量、校验云端 summary 并删除本批远端临时对象；排障时可用 `--keep-remote` 保留远端对象，再用 `cleanup-resumable` 按 `job_id` 或 `upload_session_id` 清理。P1-9 主流程验收优先使用上一节 `real_backup_pipeline_test_cli`。

```powershell
cd client
$env:UV_LINK_MODE='copy'
$env:UV_CACHE_DIR='..\.cache\uv'
$env:TMP='..\.cache\tmp'
$env:TEMP='..\.cache\tmp'
$env:CLOUD_API_BASE_URL='https://backup.baichengedu.com'
$env:BAIDU_AUTH_PASSWORD='<runtime-only-authorization-password>'

uv run python -m auto_backup_client.baidu.integration_cli --password-env BAIDU_AUTH_PASSWORD run-resumable
uv run python -m auto_backup_client.baidu.integration_cli --password-env BAIDU_AUTH_PASSWORD cleanup-resumable --job-id <job_id>
uv run python -m auto_backup_client.baidu.integration_cli --password-env BAIDU_AUTH_PASSWORD cleanup-resumable --upload-session-id <upload_session_id>
```

`integration_cli` 输出只包含 Device Token 来源、账号 ID、token version、`job_id`、`upload_session_id`、对象 hash、fs_id 和上传/同步/清理计数；不会输出 Device Token、本地路径、SQLite 路径、远端真实路径、payload、百度 token、用户密码、wrapping key 或密文 envelope。

## 远端对象校对 CLI

`reconcile_cli remote-objects` 会读取本地 SQLite 的 `remote_objects`/`upload_sessions`，调用百度 `list/listall` 获取远端对象，并生成只读脱敏差异报告。本阶段不自动删除、覆盖、重传或写入校对结果；只输出差异状态、对象类型、远端路径 SHA256、size/md5/fs_id 差异和人工处理建议。

```powershell
cd client
$env:UV_LINK_MODE='copy'
$env:UV_CACHE_DIR='..\.cache\uv'
$env:CLOUD_API_BASE_URL='https://backup.baichengedu.com'
$env:BAIDU_AUTH_PASSWORD='<runtime-only-authorization-password>'

uv run python -m auto_backup_client.baidu.integration_cli --password-env BAIDU_AUTH_PASSWORD run-resumable --keep-remote
uv run python -m auto_backup_client.baidu.reconcile_cli --password-env BAIDU_AUTH_PASSWORD remote-objects --job-id <job_id>
uv run python -m auto_backup_client.baidu.integration_cli --password-env BAIDU_AUTH_PASSWORD cleanup-resumable --job-id <job_id>
```

也可使用 `--upload-session-id <upload_session_id>` 或 `--remote-dir /apps/{appname}/backups/...` 发起校对。递归 `listall` 默认按每分钟不超过 8 次限速；`--non-recursive` 可切换到单目录 `list`。输出不得包含 Device Token、百度 token、用户密码、本地路径、SQLite 路径、payload 明文或远端真实路径。

## 远端对象人工修复 CLI

`reconcile_cli repair-remote-objects` 会先执行同一套只读校对，再把差异转换为人工修复候选动作。默认只做 dry-run，不写 SQLite、不写 `sync_outbox`、不删除、不覆盖、不重传。只有同时传入 `--apply --confirm APPLY_REMOTE_REPAIR` 时，才会把可证明的本地账本修复写入 `remote_objects` 并同事务进入 `sync_outbox`。

```powershell
cd client
$env:UV_LINK_MODE='copy'
$env:UV_CACHE_DIR='<repo>\.cache\uv'
$env:BAIDU_AUTH_PASSWORD='<runtime-only-authorization-password>'

uv run python -m auto_backup_client.baidu.reconcile_cli --password-env BAIDU_AUTH_PASSWORD repair-remote-objects --job-id <job_id>
uv run python -m auto_backup_client.baidu.reconcile_cli --password-env BAIDU_AUTH_PASSWORD repair-remote-objects --job-id <job_id> --apply --confirm APPLY_REMOTE_REPAIR
```

第一版人工修复入口只支持两类本地可审计修复：远端缺失类差异标记为 `remote_missing`，以及接受百度 `list/listall` 可证明的 size、md5、fs_id 元数据。`baidu_only`、`remote_unreadable` 和需要删除、覆盖、重传、下载读取内容的情况只输出人工处理建议。

## Cloud Sync outbox 联调 CLI

`sync-outbox` 会读取 `LOCAL_SQLITE_PATH` 或 `--sqlite-path`，执行 SQLite 迁移，复用运行时 `CLOUD_API_DEVICE_TOKEN` 或本机 DPAPI Device Token 凭据，然后调用真实云端 `POST /v1/sync/revisions`。输出只包含 `selected`、`sent`、`synced`、`conflicts`、`rejected`、`retryable` 和可选云端摘要校验计数，不输出 Device Token、payload、本地路径、SQLite 路径、百度 token、用户密码或 wrapping key。

```powershell
cd client
$env:UV_LINK_MODE='copy'
$env:UV_CACHE_DIR='..\.cache\uv'
$env:CLOUD_API_BASE_URL='https://backup.baichengedu.com'
uv run python -m auto_backup_client.sync_cli sync-outbox --verify-cloud-summary
```

发布候选阶段可先运行 Cloud Sync 同步真实性审计探针。它不依赖本地 SQLite outbox，不上传百度文件；只生成无敏感临时 revision，真实调用 `/v1/sync/revisions`，再通过 `/v1/reconcile/entities/{entity_id}` 回读校验同一 revision，并重复提交验证云端幂等 `duplicate` 语义。

```powershell
cd client
$env:UV_LINK_MODE='copy'
$env:UV_CACHE_DIR='..\.cache\uv'
$env:CLOUD_API_BASE_URL='https://backup.baichengedu.com'
uv run python -m auto_backup_client.cloud_sync_audit_cli
```

通过标准是输出 `first_sync_status: synced`、`summary_matched: true`、`duplicate_sync_status: duplicate`、`duplicate_verified: true` 和 `cloud_sync_truthful: true`。若只看到本地 `sync_outbox` 标记为 `synced`，但没有云端 summary 回读匹配，不能作为真实同步验收依据。

固定真实联调顺序：

1. 优先运行 `real_backup_pipeline_test_cli`，从临时源文件开始真实执行 P1-9 主流程，上传 archive、`.meta.json`、`job.index.json`，同步 Cloud Sync revision，远端校对一致后写入 completed 并 final sync，随后执行同路径冲突探针和百度删除清理。
2. 若需要分步排障，先运行 `upload-resumable --check-quota`，再运行 `sync-outbox --verify-cloud-summary`，最后使用 `integration_cli cleanup-resumable` 或百度 `filemanager/delete` 清理本批远端测试文件。
3. 清理失败只记录脱敏状态和人工清理待办。

现阶段只验收云端 revision 投影，不要求当前客户端的 `remote_objects` 自动进入 Go 服务 `archive_objects` 索引；若后续要联动 `GET /v1/archives/{archive_sha256}`，需要先修改并重新部署 Go 服务。

## 本地 SQLite 上传账本

默认路径沿用仓库根目录 `.env.example`：

- `LOCAL_DATA_DIR=./var/data`
- `LOCAL_SQLITE_PATH=./var/data/backup_state.sqlite3`
- `LOCAL_CACHE_DIR=./var/cache`

本地 SQLite 是任务执行主库，运行态数据库不得提交。迁移文件位于 `client/migrations/sqlite/`，当前包含 `sync_outbox`、`upload_sessions`、`upload_parts` 和 `remote_objects`。业务表写入会与 `sync_outbox` 入队在同一个 SQLite 事务内完成，后台云端同步 worker 后续阶段再接入。

## Device Token 凭据存储

当前设备的 Device Token 只用于云端 API Bearer 认证，不得写入仓库文件。

- 默认路径：`%LOCALAPPDATA%\auto_backup_bdnetdesk\credentials\device_credentials.json`。
- Windows 默认保护方式：当前用户 DPAPI。
- 可通过 `AUTO_BACKUP_DEVICE_CREDENTIAL_STORE_PATH` 指定本机凭据文件路径。
- 非 Windows 或自动化测试如需明文测试存储，必须显式设置 `AUTO_BACKUP_DEVICE_CREDENTIAL_STORE_ALLOW_PLAINTEXT=true` 或在代码中传入 `allow_plaintext=True`；真实联调不得使用明文模式。

## password KDF 参数持久化

password 模式只持久化重新派生 wrapping key 所需的 KDF salt 和 Argon2id 参数，不保存用户密码、wrapping key、百度 access token 或 refresh token。

- 默认路径：`%LOCALAPPDATA%\auto_backup_bdnetdesk\credentials\baidu_password_kdf_store.json`。
- Windows 默认保护方式：当前用户 DPAPI。
- 可通过 `AUTO_BACKUP_BAIDU_KDF_STORE_PATH` 指定本机凭据材料文件路径。
- 非 Windows 或自动化测试如需明文测试存储，必须显式设置 `AUTO_BACKUP_BAIDU_KDF_STORE_ALLOW_PLAINTEXT=true` 或在代码中传入 `allow_plaintext=True`；真实联调不得使用明文模式。

## 本地测试

```powershell
cd client
$env:UV_LINK_MODE='copy'
uv sync
uv run pytest
```

新增依赖使用 `uv add`，移除依赖使用 `uv remove`，同步环境使用 `uv sync`。不要使用 `pip install` 直接安装或变更客户端依赖。

真实 Device Token、百度 App Secret、access token 和 refresh token 不得写入本目录下的文件。
