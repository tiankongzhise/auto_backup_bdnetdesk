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
- `src/auto_backup_client/ui/baidu_settings.py`：PySide6 百度设置页，展示账号列表、设备码授权、二维码和授权完成反馈。

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
