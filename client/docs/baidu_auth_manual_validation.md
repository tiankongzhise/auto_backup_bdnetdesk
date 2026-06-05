# 百度授权客户端核心手动验收

本说明只覆盖 `client/` 目录内的客户端核心库，不覆盖后续 PySide6 UI。

## 前置条件

- Go 云端服务已部署并可访问 `https://backup.baichengedu.com`。
- PostgreSQL 迁移已执行到 `002_baidu_auth.sql`。
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

3. 在百度官方授权页完成授权后，调用 `complete_auth_session(...)`。password 模式下只传入本地派生出的 32 字节 wrapping key；不得传入或保存用户明文密码。

4. 调用 `get_token(account_id)` 后使用 `decrypt_token_envelope(...)` 在本地解密，确认明文 token 只存在于进程内存，不写日志、不写 `.env`、不写数据库或缓存文件。

5. 若需要刷新 token，运行 `refresh_baidu_account_token(...)`，确认流程先获取 `refresh-lease`，再用当前 `token_version` 作为 `expected_token_version` 回写云端。

## 需要人工停顿的场景

如果后续决定把 refresh token 刷新迁移到 Go 服务端受控执行，需要修改并重新部署 Go 服务。到该步骤时应暂停开发，等待人工完成服务器部署后再继续真实链路测试。
