# 百度网盘开放平台接口离线参考

本文件记录本项目已核验过的百度网盘开放平台官方接口资料，供后续开发离线查询和溯源使用。

## 获取记录

- 获取日期：2026-06-05
- 获取环境：Windows PowerShell，UTF-8 控制台，仓库工作区 `C:\Users\3700x\Desktop\ai\auto_backup_bdnetdesk`
- 获取顺序：浏览/搜索工具未能直接打开 `pan.baidu.com/union/doc/...`；沙箱内 `curl.exe` 连接失败；按权限流程提升后使用 `curl.exe -L --max-time 20 --silent --show-error --fail` 成功获取官方 HTML。
- 原始 HTML：仅用于本轮核对，不纳入仓库。原因是官方页面体积较大，并包含示例 access token 形态文本；仓库只保存脱敏摘要和实现所需字段。
- 实现依据：本文件摘要、`docs/product_spec_v1.3.md`、近期 Git 提交历史中已验证的真实云端授权/token 解密能力。

## 追加获取记录

- 获取日期：2026-06-07
- 获取目标：官方“获取文件列表”和“递归获取文件列表”页面。
- 获取顺序：浏览工具未能直接打开官方页面；沙箱内 `curl.exe` 连接失败；按权限流程提升后使用 `curl.exe -L --max-time 20 --silent --show-error --fail -H "User-Agent: Mozilla/5.0"` 成功获取官方 HTML。
- 临时文件：`C:\tmp\baidu-list.html` 和 `C:\tmp\baidu-listall.html`，仅用于本轮字段核对，不纳入仓库。
- 实现依据：官方 HTML 中的接口路径、参数表、响应字段、分页说明和频控说明；仓库只记录脱敏摘要。

## 文档来源

| 接口 | 官方 URL | 官方更新时间 | 本次获取结果 |
| --- | --- | --- | --- |
| 获取用户信息 | `https://pan.baidu.com/union/doc/pksg0s9ns` | 2026-05-15 | 成功，HTML 约 121 KB |
| 获取网盘容量信息 | `https://pan.baidu.com/union/doc/Cksg0s9ic` | 2022-05-28 | 成功，HTML 约 124 KB |
| 预上传 | `https://pan.baidu.com/union/doc/3ksg0s9r7` | 2022-03-08 | 成功，HTML 约 140 KB |
| 获取上传域名 | `https://pan.baidu.com/union/doc/Mlvw5hfnr` | 2024-05-07 | 成功，HTML 约 115 KB |
| 分片上传 | `https://pan.baidu.com/union/doc/nksg0s9vi` | 2022-03-09 | 成功，HTML 约 126 KB |
| 创建文件 | `https://pan.baidu.com/union/doc/rksg0sa17` | 2022-04-18 | 成功，HTML 约 126 KB |
| 管理文件 | `https://pan.baidu.com/union/doc/mksg0s9l4` | 2022-05-23 | 成功，HTML 约 151 KB |
| 获取文件列表 | `https://pan.baidu.com/union/doc/nksg0sat9` | 2022-05-29 | 成功，HTML 约 148 KB |
| 递归获取文件列表 | `https://pan.baidu.com/union/doc/Zksg0sb73` | 2022-04-20 | 成功，HTML 约 154 KB |

## 通用约束

- 所有接口均需要百度 OAuth `access_token`。
- 请求必须使用 `User-Agent: pan.baidu.com`。
- access token、refresh token、用户密码、wrapping key 和密文 token envelope 不得写入日志、文档、测试 fixture 或仓库文件。
- 本项目远端路径必须位于 `/apps/{appname}/backups` 下，并按 `{yyyy}/{MM}/{dd}/{device_id}/{job_id}` 组织。
- 本项目创建远端文件时使用 `rtype=0`，不允许百度自动重命名；遇到冲突进入后续校对流程。
- `create` 阶段 `block_list` 必须按分片序号顺序提交。

## 用户信息

用途：独立验证授权 token 可用，并读取账号相关状态。

- 方法：`GET`
- 路径：`https://pan.baidu.com/rest/2.0/xpan/nas`
- URL 参数：
  - `method=iotqueryuinfo`
  - `access_token`
  - `device_id`
- 响应要点：
  - 顶层包含 `request_id`、`error_code`、`error_msg`、`data`。
  - `error_code=0` 表示成功。
  - `data` 包含 `has_privilege`、`is_svip`、`is_iot_svip`、`start_time`、`end_time`、`now` 等字段。
- 真实差异：
  - 2026-06-05 真实联调中，按 2026-05-15 官方“获取用户信息”页调用 `GET https://pan.baidu.com/rest/2.0/xpan/nas?method=iotqueryuinfo`，当前真实授权应用或路径返回 HTTP 404。
  - 该接口不作为上传链路前置条件；真实上传批测只依赖 `quota`、`precreate`、`locateupload`、`superfile2`、`create` 和清理用 `filemanager/delete` 主链路。
  - 客户端可保留独立 `uinfo` 探针用于后续排查，但不得因 `uinfo` 404 阻断真实上传、冲突或删除清理验收。

## 网盘容量

用途：上传前检查剩余容量。

- 方法：`GET`
- 路径：`https://pan.baidu.com/api/quota`
- URL 参数：
  - `access_token`
  - `checkfree=1`
  - `checkexpire=1`
- 响应要点：
  - `errno=0` 表示成功。
  - `total`：总容量，单位 B。
  - `used`：已使用容量，单位 B。
  - `free`：免费容量，单位 B。
  - `expire`：7 天内是否有容量到期。

## 获取文件列表 list

用途：对单个任务目录或 archive 目录做非递归校对，确认 archive、`.meta.json` 和 `job.index.json` 是否存在。

- 方法：`GET`
- 路径：`https://pan.baidu.com/rest/2.0/xpan/file`
- URL 参数：
  - `method=list`
  - `access_token`
  - `dir`：待列出的目录，绝对路径；中文路径需要 URL encode。
  - `order`：排序字段，官方示例包含 `name`、`time`、`size`。
  - `desc`：是否倒序，`0` 或 `1`。
  - `start`：查询起点，默认 `0`。
  - `limit`：查询数量，官方示例使用 `10`；客户端默认按校对页需要使用分页。
  - `web`：`1` 时返回 `dir_empty` 和缩略图数据；本项目默认 `0`。
  - `folder`：`1` 只返回目录，`0` 返回全部；本项目按需要传入。
  - `showempty`：是否返回 `dir_empty`，`0` 或 `1`。
- 响应要点：
  - `errno=0` 表示成功。
  - `list`：文件或目录数组。
  - `request_id`：请求 ID。
  - 单项常用字段包括 `fs_id`、`path`、`server_filename`、`isdir`、`size`、`md5`、`category`、`server_ctime`、`server_mtime`、`local_ctime`、`local_mtime`、`dir_empty`。
- 本项目封装：
  - `BaiduNetdiskClient.list_dir(...)` 构造 `method=list` 请求并解析为 `BaiduFileListResult`。
  - 非递归 list 优先用于任务目录、archive 目录或小范围手工校对。

## 递归获取文件列表 listall

用途：对备份根目录或日期目录做递归校对，发现百度侧存在但数据库缺失的对象，或定位数据库记录对应远端对象是否仍可读。

- 方法：`GET`
- 路径：`https://pan.baidu.com/rest/2.0/xpan/multimedia`
- URL 参数：
  - `method=listall`
  - `access_token`
  - `path`：待递归列出的目录，绝对路径。
  - `recursion`：是否递归，`1` 递归，`0` 非递归。
  - `start`：查询起点，默认 `0`；当响应 `has_more=1` 时，下一次请求必须使用响应中的 `cursor`。
  - `limit`：查询数量，官方建议设置 `start` 和 `limit` 时最大为 `1000`。
  - `web`：是否返回 web 相关附加字段；本项目默认 `0`。
- 响应要点：
  - `errno=0` 表示成功。
  - `has_more=1` 表示还有下一页。
  - `cursor`：下一页查询起点。
  - `list`：递归结果数组。
  - `request_id`：请求 ID。
  - 单项常用字段包括 `category`、`fs_id`、`isdir`、`local_ctime`、`local_mtime`、`server_ctime`、`server_mtime`、`md5`、`size`、`path`、`server_filename`。
- 频控：
  - 官方页面说明 listall 命中频控时，请求频率建议不超过每分钟 8-10 次。
  - `docs/product_spec_v1.3.md` 对本项目递归列表接口约束为每分钟不超过 8 次，客户端后续校对 worker 必须按 8 次/分钟执行。
- 本项目封装：
  - `BaiduNetdiskClient.list_all(...)` 构造 `method=listall` 请求并解析分页字段。
  - `BaiduNetdiskClient.iter_list_all(...)` 在 `has_more=1` 时使用 `cursor` 继续分页。
  - 本轮只准备列表能力，不实现自动修复。

## 预上传 precreate

用途：通知百度网盘创建上传任务，获取 `uploadid` 和需要上传的分片序号。

- 方法：`POST`
- 路径：`https://pan.baidu.com/rest/2.0/xpan/file`
- URL 参数：
  - `method=precreate`
  - `access_token`
- Request body：`application/x-www-form-urlencoded`
- 必填 body 参数：
  - `path`：远端绝对路径，需要 URL encode。
  - `size`：文件大小，单位 B。
  - `isdir=0`：本项目上传 archive 和元数据文件均为文件。
  - `block_list`：文件分片 MD5 数组的 JSON 字符串；小于等于分片大小的文件只有一个 MD5。
  - `autoinit=1`：固定值。
- 可选 body 参数：
  - `rtype`：命名策略；本项目固定使用 `0`。
  - `uploadid`：已有上传任务恢复时传入。
  - `content-md5`：整个文件 MD5，32 位小写。
  - `slice-md5`：文件前 256 KiB 校验段 MD5，32 位小写。
  - `local_ctime`、`local_mtime`：本地时间戳，秒级。
- 响应要点：
  - `errno=0` 表示成功。
  - `path`：远端路径。
  - `uploadid`：上传任务 ID。
  - `return_type`：百度内部状态字段。
  - `block_list`：需要上传的分片序号列表，序号从 0 开始。

## 获取上传域名 locateupload

用途：分片上传前获取可用上传域名。

- 方法：`GET`
- 路径：`https://d.pcs.baidu.com/rest/2.0/pcs/file`
- URL 参数：
  - `method=locateupload`
  - `appid=250528`
  - `access_token`
  - `path`
  - `uploadid`
  - `upload_version=2.0`
- 响应要点：
  - `error_code=0` 表示成功。
  - `servers` 数组包含可用于上传的服务地址。
  - 优先选择 `servers` 中 `https://` 开头的地址。

## 分片上传 superfile2

用途：把本地文件分片上传到百度临时上传区。

- 方法：`POST`
- 路径：`{upload_server}/rest/2.0/pcs/superfile2`
- URL 参数：
  - `method=upload`
  - `access_token`
  - `type=tmpfile`
  - `path`
  - `uploadid`
  - `partseq`：分片序号，从 0 开始。
- Request body：`multipart/form-data`
- 表单文件字段：
  - `file`：当前分片内容。
- 响应要点：
  - `errno=0` 表示成功。
  - `md5`：服务端收到的分片 MD5。
- 分片大小限制：
  - 普通用户单分片固定 4 MiB，单文件总大小上限 4 GiB。
  - 普通会员单分片上限 16 MiB，单文件总大小上限 10 GiB。
  - 超级会员单分片上限 32 MiB，单文件总大小上限 20 GiB。

## 创建文件 create

用途：合并已上传分片，生成最终网盘文件。

- 方法：`POST`
- 路径：`https://pan.baidu.com/rest/2.0/xpan/file`
- URL 参数：
  - `method=create`
  - `access_token`
- Request body：`application/x-www-form-urlencoded`
- 必填 body 参数：
  - `path`：必须与 `precreate` 保持一致。
  - `size`：必须与真实文件大小和 `precreate` 保持一致。
  - `isdir=0`：文件。
  - `block_list`：分片 MD5 数组 JSON 字符串，必须按分片序号顺序排列，并与 `precreate`/`superfile2` 保持一致。
  - `uploadid`：`precreate` 返回的上传任务 ID。
- 可选 body 参数：
  - `rtype`：命名策略；本项目固定使用 `0`。
  - `local_ctime`、`local_mtime`：秒级时间戳。
  - 图片压缩、多版本、上传方式等参数本项目当前不使用。
- 响应要点：
  - `errno=0` 表示成功。
  - `fs_id`：网盘文件唯一标识。
  - `md5`：最终文件 MD5。
  - `server_filename`：网盘文件名。
  - `path`：最终远端路径。

## 删除测试文件 filemanager/delete

用途：真实上传联调完成后清理本批远端测试文件。

- 方法：`POST`
- 路径：`https://pan.baidu.com/rest/2.0/xpan/file`
- URL 参数：
  - `method=filemanager`
  - `access_token`
  - `opera=delete`
- Request body：`application/x-www-form-urlencoded`
- 必填 body 参数：
  - `async`：`0` 同步，`1` 自适应，`2` 异步；真实联调清理优先用 `0`。
  - `filelist`：待删除文件路径 JSON 数组字符串，例如 `["/apps/app/backups/.../file.7z"]`。
- 响应要点：
  - `errno=0` 表示请求成功。
  - `info`：文件处理结果数组。
  - `taskid`：异步任务 ID，`async=2` 时返回。
- 注意事项：
  - 官方文档说明 `delete` 操作没有 `ondup` 选项，失败时可再次发送请求。
  - 本项目仅在真实联调清理本批测试文件时使用删除接口，不用于默认清理用户原始数据。

## 本轮实现边界

- 已先落地小文件真实验证入口和核心库：`precreate -> locateupload -> superfile2 -> create`。
- 后续已补齐本地 SQLite 上传账本、`uploadid` 恢复、百度返回缺失分片 `block_list` 续传、`.meta.json` 和 `job.index.json` 生成入口。
- 当前已补齐百度 `list/listall` 客户端列表能力和官方文档摘要，但仍不实现远端校对 UI 或自动修复；后续校对必须继续以真实百度网盘 API 行为为准。
- 远端校对差异类型先按产品规格最小集表达：`db_exists_remote_missing`、`remote_meta_missing`、`remote_meta_mismatch`、`remote_size_mismatch`、`fs_id_changed`、`baidu_only`、`remote_unreadable`。
- 自动化测试覆盖纯本地路径规则、分片 MD5、SQLite 账本事务、续传分片选择和输出脱敏；百度接口契约、真实上传、冲突和删除清理必须通过真实百度网盘 API 联调验证。
