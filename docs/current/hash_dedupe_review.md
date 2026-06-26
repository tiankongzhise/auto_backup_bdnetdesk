# Hash 与去重链路专题审计

## 审计背景

本文件记录 2026-06-26 对当前项目 hash 计算、文件夹 hash、内容去重和跨设备重复上传行为的 code review 结论。审计目标是回答三个问题：

- 当前项目是否真正检查文件和归档 hash。
- 大量小文件位于文件夹中时，当前文件夹 hash 和扫描链路是否会成为瓶颈。
- 如果另一台设备已经上传了同样文件，本机后续备份是否会重复上传。

本轮只做代码审计和文档沉淀，不修改业务代码。后续如按本文优化，需要再进入正式开发小项，先更新 `api_database_contract.md`、`spec.md` 或对应测试，再改代码。

## 关键代码依据

| 主题 | 当前代码位置 | 当前职责 |
| --- | --- | --- |
| 文件扫描与完整 hash | `client/src/auto_backup_client/scan_fingerprints.py` | 递归扫描来源，计算 quick fingerprint、MD5、SHA256、`content_id` 和文件夹 hash。 |
| 内容去重索引 | `client/src/auto_backup_client/dedupe_index.py` | 按 `content_id` 汇总 `content_objects` 和 `content_references`，决定 payload/local duplicate/cloud candidate 角色。 |
| 归档与 manifest | `client/src/auto_backup_client/archive_packager.py` | 生成明文 manifest、stage payload、7-Zip 加密归档、验证 archive 和写入 `archives/archive_members`。 |
| 备份编排 | `client/src/auto_backup_client/backup_pipeline.py` | 串联 scan -> dedupe -> cloud candidates -> archive -> upload -> sync -> reconcile。 |
| 百度上传 hash | `client/src/auto_backup_client/baidu/upload.py`、`baidu/resumable_upload.py` | 计算 archive SHA256、整包 MD5、slice MD5、分片 MD5，并写上传账本。 |
| 跨设备历史导入 | `client/src/auto_backup_client/backup_history_sync.py`、`baidu/cloud_api.py` | 从 `/v1/backups?device_id=all` 拉取云端历史并导入本地 SQLite。 |
| 云端历史过滤 | `cloud-api/internal/cloudapi/server.go`、`postgres_store.go` | 只返回当前设备和同百度账号绑定设备的 `cloud_entities`。 |

## 当前 hash 检查链路

当前项目已经具备多层 hash 检查，但不同阶段的检查强度不同。

文件扫描阶段：

- `fingerprint_file()` 会先生成 quick fingerprint，再完整读取文件计算 MD5 和 SHA256。
- `content_id = sha256("v1:file:" + size + ":" + file_sha256)`，不混入文件名、路径、时间、设备或任务信息。
- 扫描前后会比较文件大小和 mtime；如果读取期间变化，`scan_status` 会标记为 `changed_during_scan`。

内容去重阶段：

- `ContentDedupeIndexer` 只处理 `scan_status == "full_hashed"` 的稳定文件。
- `_group_validated_files()` 会校验 `content_id` 必须能由 `size + sha256` 推导，拒绝不一致或碰撞。
- 本地最终去重键是 `file_sha256 + size_bytes`，quick fingerprint 不作为最终重复判断依据。

归档阶段：

- `ArchivePackager` 对 payload 文件在 stage 前再次检查 `size + sha256`。
- 归档后计算 archive SHA256/MD5。
- 通过 `7z t` 做标准验证，并解出 `manifest/manifest.json` 复算 manifest SHA256。

上传阶段：

- `BaiduResumableUploader` 对 archive 再计算 SHA256。
- `compute_file_block_plan()` 计算百度预上传需要的整包 MD5、slice MD5 和分片 MD5。
- `superfile2` 返回分片 MD5 与本地分片 MD5 不一致时会报错。

恢复阶段：

- 下载 archive 后会校验 archive SHA256。
- 恢复单个文件后会按 manifest 记录的 SHA256 复验。

审计结论：项目不是只记录 hash，而是在扫描、归档、上传和恢复多个阶段使用 hash。但仍存在“非 payload 引用复查不足”和“大量小文件性能放大”两个主要风险。

## 问题 1：非 payload 引用未在打包前复查

### 现象

`archive_packager.py` 的 `_stage_payload_members()` 只对 `payload_source` 文件进行 `size + sha256` 复查。`local_duplicate` 和 `cloud_duplicate_candidate` 不会进入 payload staging，因此不会在归档前重新读取源文件确认内容仍等于扫描时的 hash。

### 原因

当前去重角色分配逻辑是：

- 第一份需要实际入包的内容标记为 `payload_source / needs_payload`。
- 本机已有 payload archive 的重复内容标记为 `local_duplicate`。
- 云端查询命中的内容标记为 `cloud_duplicate_candidate`。

归档阶段只需要把 `payload_source` 复制到 staging 的 `payload/{content_id}`，所以只在复制 payload 时做复查。对于 duplicate 角色，归档只写 manifest/reference，不复制原文件，代码自然绕过了复查。

### 边界情况

会受影响的情况：

- 同一个 job 中两个文件扫描时内容相同，第二个文件被标记为 `local_duplicate`，但扫描后打包前第二个文件被修改。
- 另一个 job 已经归档过某内容，新 job 中同内容文件被标记为 `local_duplicate`，但新 job 源文件随后改变。
- 云端候选查询命中后，本地源文件在打包前改变。

不容易受影响的情况：

- 唯一文件或首个 payload 文件，因为 payload staging 会复查。
- 文件在扫描过程中变化，因为扫描阶段会标记 `changed_during_scan`，去重阶段会跳过不稳定文件。

### 可能后果

- 新 job 的 manifest 仍记录旧的 `content_id/file_sha256`，恢复时会恢复旧内容，而不是用户在打包时磁盘上的新内容。
- 用户以为某文件已经备份，但实际备份的是同内容重复项的旧版本。
- 清理流程后续如果依赖 content reference 判断安全性，可能对已变化的本地文件产生错误信心。当前清理前还有源文件身份复查，但这仍会增加用户困惑和排障成本。

### 优化方案

短期建议：

- 在 `ArchivePackager.package_job()` 进入 `_build_manifest_data()` 或 `_stage_payload_members()` 前，对本次 `context.file_references` 的所有本地真实路径执行一次复查。
- 对 `payload_source` 保持现有严格复查。
- 对 `local_duplicate/cloud_duplicate_candidate` 至少复查 `size + sha256`；不一致时抛出 `PayloadSourceChangedError` 或新的 `ReferencedSourceChangedError`，并触发 pipeline 已有的 rescan/rebuild index 恢复流程。

中期建议：

- 将复查逻辑从 `_stage_payload_members()` 抽出为 `validate_references_before_archive(context)`，避免 payload 和 reference 路径出现不同安全语义。
- 为 cloud candidate 单独标记为“候选命中但本地源已变更”，避免错误地继续写 manifest-only archive。

需要补充的测试：

- 同 job 重复文件中第二个 duplicate 在打包前变化，预期重新扫描或报错。
- 跨 job local duplicate 在打包前变化，预期不生成旧 hash manifest。
- cloud candidate 文件在候选查询后变化，预期不生成 manifest-only archive。

## 问题 2：大量小文件扫描存在性能瓶颈

### 现象

当前 `fingerprint_file()` 对每个文件执行两轮读取：

1. quick fingerprint 读取采样范围。
2. 完整 MD5/SHA256 读取全文件。

对小于等于 16MiB 的文件，quick fingerprint 的采样范围就是整个文件。这意味着大量小文件会被完整读两遍。

### 原因

quick fingerprint 设计用于大文件候选筛选，完整 hash 用于最终去重。当前实现为了统一路径，先算 quick fingerprint，再算完整 MD5/SHA256，没有在“小文件 quick 已经完整读取”的情况下复用同一轮读取结果。

同时，扫描过程是同步串行递归：

- `Path.iterdir()` 逐目录读取并按名称排序。
- 每个普通文件串行计算 hash。
- 扫描结果先在内存中积累 drafts，最后统一写入 SQLite。

### 边界情况

影响最明显的目录：

- 数万到数十万个小文件。
- 小文件位于机械硬盘、移动硬盘或网络盘。
- 多层深目录，包含大量子目录。
- Windows Defender 或其他安全软件对每次读取都有额外拦截成本。

影响较小的目录：

- 少量大文件。大文件 quick fingerprint 只采样，完整 hash 仍是主要成本。
- SSD 上中等数量文件，瓶颈可能仍可接受。

### 可能后果

- 用户看到扫描阶段长时间运行，误以为程序卡住。
- 大量小文件会导致 CPU、磁盘 IO 和 SQLite 写入同时上升。
- 如果扫描耗时过长，源文件在扫描和归档之间变化的概率上升，触发更多 `changed_during_scan` 或后续复查失败。
- 本地 outbox 事件增多，云同步积压，R10 断网补偿恢复后可能产生较长同步时间。

### 优化方案

短期建议：

- 对 `size <= QUICK_FULL_READ_LIMIT` 的文件，只读取一次，同时更新 quick SHA256、MD5 和完整 SHA256。
- 在 UI/operation DTO 中拆分展示“枚举文件数 / 已 hash 文件数 / 当前阶段”，避免用户误判卡死。

中期建议：

- 引入增量扫描缓存：如果 `file_identity + size + mtime_ns + attrs` 未变化，可复用上次 MD5/SHA256/content_id。
- 对不可稳定获取 `file_identity` 的文件系统，退化为 `path + size + mtime_ns` 快速命中，但命中后仍在风险场景做抽样或完整 hash 校验。
- 增加有限并发 hash worker，默认小并发，例如 2-4，避免拖垮机械盘和 UI。

长期建议：

- 将 scan results 改为 streaming 写入和进度上报，不必完整扫描后再统一写库。
- 为大目录提供可暂停/恢复的扫描 checkpoint，避免一次运行失败后全部重来。

需要补充的测试：

- 单个小文件只读一轮的单元测试，可用 fake file reader 或 monkeypatch 计数。
- 10k 小文件基准测试，记录扫描耗时、SQLite 写入数和 outbox 数。
- 扫描进度 DTO 测试，确认 UI 能展示 hash 进展。

## 问题 3：文件夹 hash 计算会在深层目录中放大

### 现象

`_finalize_folder_hashes()` 对每个文件夹都会调用 `_flatten_files(folder)` 和 `_flatten_folders(folder)` 来生成该文件夹的 manifest entries。对每一层目录都重新遍历其全部子孙，深层树会产生重复遍历。

### 原因

当前文件夹有两个 hash：

- `folder_content_hash`：只基于直接子文件 content_id 和直接子文件夹 content hash，忽略路径和名称。
- `folder_manifest_hash`：基于当前文件夹下全部子孙文件/文件夹的相对路径、名称、size、ctime、mtime、atime、attrs 和 content_id。

为了让每个文件夹都拥有完整 manifest hash，当前实现对每个文件夹单独 flatten 子树。这个逻辑简单可靠，但对深层大量小文件不够经济。

### 边界情况

瓶颈明显的结构：

- 深链路目录，例如 100 层目录，每层都有文件。
- 每层目录都包含大量子目录和文件。
- 文件数不一定巨大，但目录深度很大。

瓶颈较小的结构：

- 扁平目录，只有一两层。
- 文件数量多但目录层级浅，重复 flatten 成本低于完整文件 hash 成本。

### 可能后果

- 文件夹 hash 阶段在深层目录中出现二次放大，整体扫描时间不再接近 O(N)。
- 内存短时占用升高，因为每个文件夹都会构建 manifest entry 列表。
- folder_items revision 变多，云端历史导入和恢复候选计算负担增加。

### 优化方案

短期建议：

- 保留 `folder_content_hash` 现有递归聚合方式，不改产品语义。
- 对 `folder_manifest_hash` 增加注释和测试，明确它是“结构/元数据 hash”，不是纯内容 hash。

中期建议：

- `_finalize_folder_hashes()` 返回子树聚合结果，父目录复用子目录已计算好的 manifest token，而不是每层重新 flatten。
- 如果恢复流程只需要来源级根目录 manifest hash，可考虑只为 source root 计算完整 manifest hash，子目录只保留 `folder_content_hash` 和直接子项摘要。

长期建议：

- 将 folder manifest hash 设计成 Merkle tree：每个目录 hash 只由直接子项摘要组成，子项摘要包含路径段、类型、内容 hash 和必要元数据。这样每个节点只处理直接子项，总体接近 O(N)。

需要补充的测试：

- 构造深层目录，统计 `_flatten_files/_flatten_folders` 调用次数或运行时间。
- 对比 Merkle 版本和当前版本在重命名、移动、内容改变、mtime 改变时的 hash 变化语义。

## 问题 4：folder manifest hash 包含 atime，容易抖动

### 现象

文件和文件夹 manifest entries 中都包含 `atime_ns`。如果文件系统更新访问时间，备份程序读取文件本身就可能改变 atime，导致后续扫描的 `folder_manifest_hash` 变化。

### 原因

manifest hash 当前意图追踪结构和元数据，因此纳入了 `ctime_ns`、`mtime_ns`、`atime_ns` 和 attrs。问题在于 atime 并不代表内容变化，也不一定代表用户主动修改；它可能由读取、索引、杀毒软件或系统策略改变。

### 边界情况

风险取决于文件系统和系统设置：

- Windows NTFS 默认行为可能不频繁更新 last access，但不同版本、策略或外部盘设置可能不同。
- 网络盘、NAS、移动盘可能有自己的 atime 行为。
- 杀毒、索引器或备份程序自身读取文件都可能改变 atime。

### 可能后果

- 文件内容未变，folder manifest hash 仍变化。
- 重扫后 folder_items 产生新 revision 和 outbox 事件。
- 跨设备比较文件夹 hash 时出现“内容一致但 manifest 不一致”的噪声。
- 用户看到云端同步或校对差异，但实际只是访问时间变化。

### 优化方案

短期建议：

- 在文档和 UI 诊断中区分 `folder_content_hash` 和 `folder_manifest_hash`：前者用于内容等价，后者用于结构/元数据变更。
- 不把 `folder_manifest_hash` 作为最终去重依据。

中期建议：

- 从 `folder_manifest_hash` 中移除 `atime_ns`，或将其拆成单独的 `folder_metadata_hash`。
- 保留 mtime/attrs 是否参与 hash 需要按恢复需求决定；如果恢复要尽量保留元数据，可以记录但不参与内容等价判断。

长期建议：

- 建立三类 hash：内容 hash、结构 hash、元数据 hash。不同 UI 和同步场景使用不同 hash，避免一个 hash 同时承担所有语义。

需要补充的测试：

- 修改 atime 但不改内容，确认内容 hash 不变、结构 hash 不变、元数据 hash 可变。
- 修改文件名或目录结构，确认结构 hash 变化。
- 修改文件内容，确认内容 hash 和结构/manifest 相关 hash 都按预期变化。

## 问题 5：重扫无条件删除重插，制造 SQLite/outbox 压力

### 现象

`SQLiteClientStore.replace_scan_results_for_source()` 每次扫描前会删除当前 source 的 `file_items` 和 `folder_items`，然后 scanner 会重新插入所有文件/文件夹并 enqueue revision。

### 原因

这种实现简化了“文件删除、移动、重命名”的处理：先清空旧扫描结果，再写入当前快照。但是它没有比较 canonical hash 是否真正变化，也没有跳过未变文件。

### 边界情况

影响明显的情况：

- 用户多次继续同一个大目录任务。
- 备份失败后重试，scan 阶段重新跑。
- 目录内容稳定，但文件数量很多。
- 云端暂时不可用，outbox 已积压，重扫又制造更多事件。

影响较小的情况：

- 一次性小任务。
- 文件数量少，或用户很少重试。

### 可能后果

- SQLite 写入量和 WAL/数据库增长增加。
- `sync_outbox` 大量重复 revision，Cloud Sync 工作量增加。
- 云端 `cloud_entities/entity_revisions` 历史膨胀。
- UI 最近任务和同步状态更新延迟。

### 优化方案

短期建议：

- 在 `put_file_item/put_folder_item` 前比较新 payload 的 `canonical_record_sha256` 和旧行；如果未变化，不写新 revision。
- 对删除场景不能直接静默丢弃，应生成 delete revision 或软删除标记，避免云端保留已删除文件项。

中期建议：

- 将 `replace_scan_results_for_source()` 改为 diff 模式：列出现有 item_id 集合，扫描时 upsert changed/new，扫描结束后对未见 item 标记 deleted。
- 同步 payload 中避免把易抖动字段用于 canonical hash，尤其是 atime。

长期建议：

- 建立扫描快照表和 item 表分离：每次 scan 有 snapshot_id，文件项按内容和路径版本复用，避免同一 item 重复写 revision。

需要补充的测试：

- 同一目录未变化连续扫描两次，期望第二次不新增 file/folder outbox。
- 删除文件后重扫，期望产生可同步的 delete/soft delete 事件。
- 仅 atime 改变后重扫，按新语义确认是否应新增 revision。

## 问题 6：跨设备去重不是强保证

### 当前行为

当前项目有两条跨设备去重相关路径：

1. `DeviceBackupHistoryRefresher` 默认调用 `/v1/backups?device_id=all`，把同一百度账号绑定设备的 `content_objects/content_references/archives/archive_members/remote_objects` 导入本机 SQLite。
2. `ContentDedupeIndexer.refresh_cloud_candidates()` 可调用 `/v1/contents/{content_id}` 查询云端内容候选，只有云端返回的 `file_sha256 + size_bytes` 和本地一致时才标记 `cloud_duplicate_candidate`。

pywebview 默认启动备份时没有开启 `refresh_cloud_candidates`。因此 UI 里的常规备份主要依赖“打开页面或列任务时的跨设备历史导入”。

### 何时不会重复上传 payload

本机通常不会重复上传 payload 的条件：

- 另一台设备已成功完成备份。
- 另一台设备的 `content_objects/content_references/archive_members/remote_objects` 已成功同步到云端。
- 当前设备与另一台设备绑定同一百度账号。
- 当前设备在备份前成功刷新并导入跨设备历史。
- 导入的历史中能找到已有 payload reference，`ContentDedupeIndexer` 将新文件标记为 `local_duplicate` 或相关 duplicate 状态。

在这些条件满足时，本机可能只生成 manifest-only 或 reference archive，不再上传同样 payload。

### 何时仍会重复上传

仍可能重复上传 payload 的情况：

- 当前设备备份前没有刷新历史，或 `_refresh_history()` 静默失败。
- 另一台设备完成上传但 outbox 尚未同步到云端。
- 云端 `/v1/backups?device_id=all` 结果被 `limit` 截断，未导入相关 content/archive 记录。
- 两台设备没有绑定同一百度账号，云端历史不可见。
- 旧服务端不支持 `device_id=all`，客户端回退到 `current`。
- UI 默认没有开启 `refresh_cloud_candidates`，不会在备份当下逐 content 查询云端。

### 可能后果

- 同一内容在不同设备重复生成和上传 payload archive，占用百度网盘空间。
- 去重行为对用户不稳定：有时因为刚好刷新历史而跳过，有时因为历史没刷新而重复上传。
- 恢复链路可能出现 manifest-only archive 引用其他 archive 的信息不足，尤其是跨设备历史导入不完整时。

### 优化方案

短期建议：

- 在 `start_job` 前执行一次显式历史刷新，并把刷新结果写入 operation DTO；失败时提示“跨设备去重不可用，本次可能重复上传”，但不阻塞备份。
- 在 UI 上传参数中增加“检查云端重复内容”开关，默认开启或至少在发布候选中可见。
- 开启 `refresh_cloud_candidates` 后，对 query 失败的 content 记录 `retryable_error`，不要误标重复。

中期建议：

- 把 `refresh_cloud_candidates` 纳入 pywebview 默认 `BackupPipelineOptions`，当 `sync_outbox=True` 且 cloud client 可用时自动执行。
- 为 `/v1/backups` 增加分页游标或按 entity_type 增量拉取，避免 `limit=5000` 截断关键索引。
- 在导入跨设备历史后校验同一 content_id 是否同时有可用 payload archive 和 remote object；否则只标记为候选，不直接跳过 payload。

长期建议：

- 设计服务端“内容可恢复性查询”接口，不只返回 `content_id/file_sha256/size`，还返回可访问 archive、remote_path、设备/账号可见性和元数据完整性。
- 客户端只在云端证明“内容可恢复”时创建 manifest-only/reference archive，否则仍上传 payload。

需要补充的测试：

- pywebview `start_job` 默认触发历史刷新并记录结果。
- 历史刷新失败时仍能备份，但 UI 提示可能重复上传。
- 另设备内容同步完整时，本机不上传 payload。
- 只有 content object、没有可用 archive/remote object 时，本机不应盲目跳过 payload。

## 问题 7：跨设备 manifest-only 恢复引用不够显式

### 现象

`archive_packager.py` 的 `_payload_archive_references()` 只查询当前 job 内的 payload archive members。跨 job 或跨设备导入的 payload archive 不会被写入新 manifest 的 `referenced_archive_id/referenced_archive_remote_path`。

恢复时，`restore_flow.py` 的 `_external_payload_source()` 会通过本地 SQLite 的 `archive_members` 按 `content_id` 查找其他 payload archive。因此只要历史完整导入，恢复仍可能成功。但 manifest 自身缺少显式引用，跨设备历史不完整时可诊断性和可携带性较差。

### 边界情况

可能成功的情况：

- 本地 SQLite 已完整导入提供 payload 的其他 job/设备的 `archive_members`、`archives` 和 `remote_objects`。
- 恢复时 SQLite 仍可查询到这些外部 payload。

可能失败或难以诊断的情况：

- 用户只拿到 manifest-only archive 和 `.meta.json`，没有完整本地 SQLite 历史。
- 云端历史导入 limit 截断，缺少 `archive_members` 或 `remote_objects`。
- 另设备 archive 仍在百度网盘，但当前 manifest 没写远端引用，恢复只能依赖数据库猜测。

### 可能后果

- manifest-only archive 的可携带性不足。
- 跨设备恢复排障时，用户看到“payload 不可用”，但 manifest 里没有明确说明应下载哪个 archive。
- 如果未来支持导出/迁移备份索引，manifest-only archive 需要额外数据库才能恢复。

### 优化方案

短期建议：

- 扩展 `_payload_archive_references()`，允许查找同 content_id 的任意 payload archive member，并优先选择 remote_confirmed 或 `remote_objects.status='remote_created'` 的 archive。
- 在 manifest item 中写入 `referenced_archive_id` 和 `referenced_archive_remote_path`，即使引用来自其他 job 或其他设备历史。

中期建议：

- 为 `archive_members` 增加索引或查询封装，按 `content_id/member_type/status` 快速查可恢复 payload。
- 在打包 manifest-only archive 前校验每个 reference 都能找到可恢复 payload；找不到则退回 payload_source 上传或阻止完成。

长期建议：

- 建立“content availability”本地表，汇总每个 content_id 的本地 archive、远端 archive、账号可见性、恢复密码要求和最近校对状态。

需要补充的测试：

- 跨 job local duplicate manifest 中写入 referenced archive。
- 跨设备历史导入后 manifest-only archive 写入 referenced remote path。
- 找不到可恢复 payload 时不生成 manifest-only archive。

## 推荐优化优先级

| 优先级 | 工作项 | 原因 |
| --- | --- | --- |
| P0 | 打包前复查所有 content references | 直接影响备份正确性，避免记录旧内容。 |
| P0 | manifest-only/reference archive 必须能证明 payload 可恢复 | 直接影响恢复可靠性和跨设备去重安全性。 |
| P1 | UI 启动备份前显式历史刷新并提示结果 | 减少跨设备重复上传的不确定性。 |
| P1 | 小文件 hash 单次读取优化 | 降低大量小文件扫描成本，改动相对局部。 |
| P1 | 重扫 diff 化，未变文件不新增 revision | 降低 SQLite/outbox/云端同步压力。 |
| P2 | folder manifest hash 去 atime 或拆分语义 | 降低 hash 抖动和误报差异。 |
| P2 | 文件夹 hash Merkle 化 | 优化深层目录性能，但需要更多设计和回归测试。 |
| P2 | 服务端内容可恢复性查询接口 | 让跨设备去重从“候选”升级为“可恢复证明”。 |

## 后续验收建议

后续进入实现时，建议新增一个专门的测试分组：

- hash correctness：验证 `content_id`、folder content hash、manifest hash 的语义。
- source mutation：验证 scan 后、archive 前文件变化不会产生错误 manifest。
- small files benchmark：记录大量小文件扫描耗时和 outbox 事件数。
- cross-device dedupe：验证另一设备上传后，本机在历史完整导入、历史缺失、云端候选命中、云端候选失败四种情况下的上传行为。
- restore availability：验证 manifest-only archive 的每个 reference 都能定位到本地或远端 payload archive。

这些测试中，云端/百度真实契约仍按项目规则走真实 API；纯本地 hash、SQLite diff 和 manifest 语义可用单元测试覆盖。
