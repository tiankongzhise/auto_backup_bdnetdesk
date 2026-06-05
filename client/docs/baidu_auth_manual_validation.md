# 百度授权客户端核心手动验收

本说明覆盖 `client/` 目录内的百度授权核心库、真实联调 CLI 和 PySide6 百度设置页。

## 前置条件

- Go 云端服务已部署并可访问 `https://backup.baichengedu.com`。
- 新版 Go 云端服务已重启；`cloud-api serve` 启动时会自动检查并执行内置 PostgreSQL 迁移，不需要在客户端联调前手动初始化数据库。
- `https://backup.baichengedu.com/v1/readyz` 返回 200；如果返回 `schema_not_ready`，先检查服务器启动日志中的自动迁移错误和实际 PostgreSQL 连接配置，不要继续客户端授权联调。
- 已通过设备注册接口拿到本机 `device_token`。
- 真实 Device Token、百度 App Secret、access token、refresh token 和用户备份密码只允许在运行时输入或保存在 Windows DPAPI/凭据管理器，不得写入仓库文件。

## 验收步骤

1. 在 `client/` 目录安装客户端依赖并运行测试。

   ```powershell
   cd client
   $env:UV_LINK_MODE='copy'
   uv sync
   uv run pytest
   ```

2. 使用 `BaiduCloudClient` 调用 `create_auth_session(flow="device_code")`，确认返回 `user_code`、`verification_url` 和 `qrcode_url`，响应中不包含百度 `device_code`、access token 或 refresh token。

3. 在百度官方授权页完成授权后，调用 `complete_auth_session(...)`。password 模式下只传入本地派生出的 32 字节 wrapping key；不得传入或保存用户明文密码。客户端会按 `account_id` 保存 KDF salt 和 Argon2id 参数，用于后续用同一授权密码重新派生同一个 wrapping key。

4. 重启客户端进程后，调用 `token-check` 或 UI “验证解密”，确认本地 KDF 参数可以重新派生 wrapping key，并成功解密云端密文 token。明文 token 只存在于进程内存，不写日志、不写 `.env`、不写数据库或缓存文件。

5. 若需要刷新 token，运行 `refresh_baidu_account_token(...)`，确认流程先获取 `refresh-lease`，再用当前 `token_version` 作为 `expected_token_version` 回写云端。

## KDF 参数存储

password 模式只持久化 KDF salt、Argon2id 参数、`account_id` 和 token version 元数据，不保存用户密码、wrapping key、百度 access token 或 refresh token。

- Windows 默认保存到 `%LOCALAPPDATA%\auto_backup_bdnetdesk\credentials\baidu_password_kdf_store.json`，并使用当前用户 DPAPI 保护。
- 如需改存储位置，可设置 `AUTO_BACKUP_BAIDU_KDF_STORE_PATH`。
- 非 Windows 或自动化测试只有显式设置 `AUTO_BACKUP_BAIDU_KDF_STORE_ALLOW_PLAINTEXT=true` 或代码传入 `allow_plaintext=True` 时才允许明文测试存储；真实联调不得使用明文模式。

## 真实 CLI 联调

```powershell
cd client
$env:UV_LINK_MODE='copy'
uv run python -m auto_backup_client.baidu.real_auth_cli health
$env:CLOUD_API_DEVICE_TOKEN='<runtime-only-device-token>'
$env:BAIDU_AUTH_PASSWORD='<runtime-only-authorization-password>'
uv run python -m auto_backup_client.baidu.real_auth_cli accounts
uv run python -m auto_backup_client.baidu.real_auth_cli device-code --password-env BAIDU_AUTH_PASSWORD
uv run python -m auto_backup_client.baidu.real_auth_cli token-check --password-env BAIDU_AUTH_PASSWORD
```

没有 Device Token 时，可加 `--register-ephemeral-device` 临时注册设备；脚本只在当前进程内使用返回的 token，不写入文件。`token-check` 只输出账号 ID、token version、过期时间、token type 和 scope 等脱敏元数据，不输出 access token 或 refresh token。

## PySide6 UI 联调

```powershell
cd client
$env:UV_LINK_MODE='copy'
$env:CLOUD_API_BASE_URL='https://backup.baichengedu.com'
$env:CLOUD_API_DEVICE_TOKEN='<runtime-only-device-token>'
uv run python -m auto_backup_client.ui.baidu_settings
```

页面会调用真实云端 API 展示账号列表、选择账号、创建设备码授权 session、显示授权地址/用户码/二维码，并在授权完成后提交本地派生 wrapping key 完成密文 token 入库。完成授权后可重启页面，输入同一授权密码并点击“验证解密”，确认 KDF 参数持久化材料可恢复云端密文 token 本地解密能力。

## 需要人工停顿的场景

如果后续决定把 refresh token 刷新迁移到 Go 服务端受控执行，需要修改并重新部署 Go 服务。到该步骤时应暂停开发，等待人工完成服务器部署后再继续真实链路测试。
