from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping


DEFAULT_CLOUD_API_BASE_URL = "https://backup.baichengedu.com"
DEFAULT_BAIDU_TOKEN_URL = "https://openapi.baidu.com/oauth/2.0/token"
LOCAL_APP_DIR_NAME = "auto_backup_bdnetdesk"
DEFAULT_LOCAL_DATA_DIR = str(Path.home() / "AppData" / "Local" / LOCAL_APP_DIR_NAME / "data")
DEFAULT_LOCAL_SQLITE_PATH = str(Path(DEFAULT_LOCAL_DATA_DIR) / "backup_state.sqlite3")
DEFAULT_LOCAL_CACHE_DIR = str(Path.home() / "AppData" / "Local" / LOCAL_APP_DIR_NAME / "cache")


@dataclass(frozen=True)
class ClientSettings:
    cloud_api_base_url: str
    device_token: str
    baidu_token_url: str = DEFAULT_BAIDU_TOKEN_URL
    local_data_dir: str = DEFAULT_LOCAL_DATA_DIR
    local_sqlite_path: str = DEFAULT_LOCAL_SQLITE_PATH
    local_cache_dir: str = DEFAULT_LOCAL_CACHE_DIR

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ClientSettings":
        source = os.environ if env is None else env
        default_data_dir = default_local_data_dir(source)
        local_data_dir = _read_text(source, "LOCAL_DATA_DIR", default_data_dir)
        return cls(
            cloud_api_base_url=_read_text(source, "CLOUD_API_BASE_URL", DEFAULT_CLOUD_API_BASE_URL),
            device_token=_read_text(source, "CLOUD_API_DEVICE_TOKEN", ""),
            baidu_token_url=_read_text(source, "BAIDU_TOKEN_URL", DEFAULT_BAIDU_TOKEN_URL),
            local_data_dir=local_data_dir,
            local_sqlite_path=_read_text(source, "LOCAL_SQLITE_PATH", str(Path(local_data_dir) / "backup_state.sqlite3")),
            local_cache_dir=_read_text(source, "LOCAL_CACHE_DIR", default_local_cache_dir(source)),
        )

    def validate(self, *, require_device_token: bool = True) -> None:
        if not self.cloud_api_base_url:
            raise ValueError("CLOUD_API_BASE_URL is required")
        if require_device_token and not self.device_token:
            raise ValueError("CLOUD_API_DEVICE_TOKEN is required")
        if not self.baidu_token_url:
            raise ValueError("BAIDU_TOKEN_URL is required")
        if not self.local_data_dir:
            raise ValueError("LOCAL_DATA_DIR is required")
        if not self.local_sqlite_path:
            raise ValueError("LOCAL_SQLITE_PATH is required")
        if not self.local_cache_dir:
            raise ValueError("LOCAL_CACHE_DIR is required")


def _read_text(source: Mapping[str, str], key: str, default: str) -> str:
    value = source.get(key, default)
    return value.strip() if isinstance(value, str) else default


def default_local_app_root(env: Mapping[str, str] | None = None) -> Path:
    source = os.environ if env is None else env
    local_app_data = _read_text(source, "LOCALAPPDATA", "")
    if local_app_data:
        return Path(local_app_data) / LOCAL_APP_DIR_NAME
    return Path.home() / "AppData" / "Local" / LOCAL_APP_DIR_NAME


def default_local_data_dir(env: Mapping[str, str] | None = None) -> str:
    return str(default_local_app_root(env) / "data")


def default_local_cache_dir(env: Mapping[str, str] | None = None) -> str:
    return str(default_local_app_root(env) / "cache")
