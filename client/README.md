# Python 客户端

客户端源码、测试、迁移和客户端专用说明集中放在本目录，避免把客户端实现散布到仓库根目录。

## 当前范围

- `src/auto_backup_client/settings.py`：客户端运行配置读取。
- `src/auto_backup_client/redaction.py`：敏感字段脱敏工具。
- `src/auto_backup_client/baidu/cloud_api.py`：Go 云端百度授权 API 客户端。
- `src/auto_backup_client/baidu/auth_workflow.py`：百度授权 UI/CLI 复用的真实授权流程控制器。
- `src/auto_backup_client/baidu/real_auth_cli.py`：真实云端 API 联调命令行入口。
- `src/auto_backup_client/baidu/crypto.py`：百度密文 token envelope 的本地加解密。
- `src/auto_backup_client/baidu/refresh.py`：refresh token 租约、百度 token 刷新和云端版本回写流程。
- `src/auto_backup_client/ui/baidu_settings.py`：PySide6 百度设置页，展示账号列表、设备码授权、二维码和授权完成反馈。

## PySide6 百度设置页

运行前通过运行时环境提供真实云端配置：

```powershell
cd client
$env:UV_LINK_MODE='copy'
$env:CLOUD_API_BASE_URL='https://backup.baichengedu.com'
$env:CLOUD_API_DEVICE_TOKEN='<runtime-only-device-token>'
uv run python -m auto_backup_client.ui.baidu_settings
```

`CLOUD_API_DEVICE_TOKEN` 不得写入仓库文件。页面会直接调用真实云端 API 读取账号、选择账号、创建设备码授权 session、轮询状态并完成密文 token 写入。

## 真实联调 CLI

检查真实云端：

```powershell
cd client
$env:UV_LINK_MODE='copy'
uv run python -m auto_backup_client.baidu.real_auth_cli health
```

读取账号或启动设备码授权需要运行时 Device Token：

```powershell
$env:CLOUD_API_DEVICE_TOKEN='<runtime-only-device-token>'
uv run python -m auto_backup_client.baidu.real_auth_cli accounts
uv run python -m auto_backup_client.baidu.real_auth_cli device-code --password-env BAIDU_AUTH_PASSWORD
```

如果测试环境尚无 Device Token，可使用 `--register-ephemeral-device` 在真实云端临时注册设备，token 只在当前进程内使用，不会写入文件。

## 本地测试

```powershell
cd client
$env:UV_LINK_MODE='copy'
uv sync
uv run pytest
```

新增依赖使用 `uv add`，移除依赖使用 `uv remove`，同步环境使用 `uv sync`。不要使用 `pip install` 直接安装或变更客户端依赖。

真实 Device Token、百度 App Secret、access token 和 refresh token 不得写入本目录下的文件。
