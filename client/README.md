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
