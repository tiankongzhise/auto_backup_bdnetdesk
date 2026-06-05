# Python 客户端

客户端源码、测试、迁移和客户端专用说明集中放在本目录，避免把客户端实现散布到仓库根目录。

## 当前范围

- `src/auto_backup_client/settings.py`：客户端运行配置读取。
- `src/auto_backup_client/redaction.py`：敏感字段脱敏工具。
- `src/auto_backup_client/baidu/cloud_api.py`：Go 云端百度授权 API 客户端。
- `src/auto_backup_client/baidu/crypto.py`：百度密文 token envelope 的本地加解密。
- `src/auto_backup_client/baidu/refresh.py`：refresh token 租约、百度 token 刷新和云端版本回写流程。

## 本地测试

```powershell
cd client
$env:UV_LINK_MODE='copy'
uv sync
uv run pytest
```

新增依赖使用 `uv add`，移除依赖使用 `uv remove`，同步环境使用 `uv sync`。不要使用 `pip install` 直接安装或变更客户端依赖。

真实 Device Token、百度 App Secret、access token 和 refresh token 不得写入本目录下的文件。
