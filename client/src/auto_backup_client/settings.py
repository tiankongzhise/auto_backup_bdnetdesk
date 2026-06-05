from __future__ import annotations

from dataclasses import dataclass
from os import environ
from typing import Mapping


DEFAULT_CLOUD_API_BASE_URL = "https://backup.baichengedu.com"
DEFAULT_BAIDU_TOKEN_URL = "https://openapi.baidu.com/oauth/2.0/token"


@dataclass(frozen=True)
class ClientSettings:
    cloud_api_base_url: str
    device_token: str
    baidu_token_url: str = DEFAULT_BAIDU_TOKEN_URL

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ClientSettings":
        source = environ if env is None else env
        return cls(
            cloud_api_base_url=_read_text(source, "CLOUD_API_BASE_URL", DEFAULT_CLOUD_API_BASE_URL),
            device_token=_read_text(source, "CLOUD_API_DEVICE_TOKEN", ""),
            baidu_token_url=_read_text(source, "BAIDU_TOKEN_URL", DEFAULT_BAIDU_TOKEN_URL),
        )

    def validate(self, *, require_device_token: bool = True) -> None:
        if not self.cloud_api_base_url:
            raise ValueError("CLOUD_API_BASE_URL is required")
        if require_device_token and not self.device_token:
            raise ValueError("CLOUD_API_DEVICE_TOKEN is required")
        if not self.baidu_token_url:
            raise ValueError("BAIDU_TOKEN_URL is required")


def _read_text(source: Mapping[str, str], key: str, default: str) -> str:
    value = source.get(key, default)
    return value.strip() if isinstance(value, str) else default
