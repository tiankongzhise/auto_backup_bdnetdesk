# 百度网盘开放平台接口离线参考

本文件记录本项目已核验过的百度网盘开放平台官方接口资料，供后续开发离线查询和溯源使用。

## 获取记录

- 获取日期：2026-06-05
- 获取环境：Windows PowerShell，UTF-8 控制台，仓库工作区 `C:\Users\3700x\Desktop\ai\auto_backup_bdnetdesk`
- 获取顺序：浏览/搜索工具未能直接打开 `pan.baidu.com/union/doc/...`；沙箱内 `curl.exe` 连接失败；按权限流程提升后使用 `curl.exe -L --max-time 20 --silent --show-error --fail` 成功获取官方 HTML。
- 原始 HTML：仅用于本轮核对，不纳入仓库。原因是官方页面体积较大，并包含示例 access token 形态文本；仓库只保存脱敏摘要和实现所需字段。
- 实现依据：本文件摘要、`docs/product_spec_v1.3.md`、近期 Git 提交历史中已验证的真实云端授权/token 解密能力。

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

- 本轮先落地小文件真实验证入口和核心库：`precreate -> locateupload -> superfile2 -> create`。
- 本轮不提交本地 SQLite 上传状态表；断点续传数据库态、uploadid 恢复、远端校对和 `.meta.json`/`job.index.json` 完整生产流程后续阶段补齐。
- 自动化测试只覆盖纯本地路径规则和分片 MD5 计算；百度接口契约、上传、冲突和删除清理必须通过真实百度网盘 API 联调验证。
