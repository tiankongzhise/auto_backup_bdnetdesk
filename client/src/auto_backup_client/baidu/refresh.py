from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from auto_backup_client.baidu.cloud_api import BaiduCloudClient
from auto_backup_client.baidu.crypto import (
    BAIDU_ENCRYPTION_PASSWORD,
    PlainBaiduToken,
    decrypt_token_envelope,
    encrypt_password_token,
)
from auto_backup_client.baidu.models import BaiduEncryptedToken


class RefreshLeaseUnavailable(RuntimeError):
    pass


class BaiduOAuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class BaiduOAuthRefreshResult:
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str = "Bearer"
    scope: str = ""


class BaiduOAuthTokenClient:
    def __init__(
        self,
        token_url: str,
        app_key: str,
        app_secret: str,
        *,
        http_client: httpx.Client | None = None,
        timeout: float = 20.0,
    ) -> None:
        if not app_key:
            raise ValueError("baidu app key is required")
        if not app_secret:
            raise ValueError("baidu app secret is required for refresh_token grant")
        self._token_url = token_url
        self._app_key = app_key
        self._app_secret = app_secret
        self._client = http_client or httpx.Client(timeout=timeout)
        self._owns_client = http_client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "BaiduOAuthTokenClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def refresh_access_token(self, refresh_token: str) -> BaiduOAuthRefreshResult:
        response = self._client.post(
            self._token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self._app_key,
                "client_secret": self._app_secret,
            },
        )
        if response.status_code >= 400:
            raise BaiduOAuthError(f"baidu token endpoint returned {response.status_code}")
        data = response.json()
        if not isinstance(data, dict):
            raise BaiduOAuthError("baidu token response must be a JSON object")
        if data.get("error"):
            raise BaiduOAuthError(str(data.get("error_description") or data.get("error")))
        access_token = str(data.get("access_token", ""))
        if not access_token:
            raise BaiduOAuthError("baidu token response missed access_token")
        expires_in = int(data.get("expires_in", 0))
        if expires_in <= 0:
            raise BaiduOAuthError("baidu token response missed expires_in")
        return BaiduOAuthRefreshResult(
            access_token=access_token,
            refresh_token=str(data.get("refresh_token", "") or refresh_token),
            expires_in=expires_in,
            token_type=str(data.get("token_type", "Bearer") or "Bearer"),
            scope=str(data.get("scope", "")),
        )


def refresh_baidu_account_token(
    *,
    cloud_client: BaiduCloudClient,
    oauth_client: BaiduOAuthTokenClient,
    account_id: str,
    password_wrapping_key: bytes,
    lease_id: str | None = None,
    lease_duration_seconds: int = 300,
    now: datetime | None = None,
) -> BaiduEncryptedToken:
    actual_lease_id = lease_id or f"client-{uuid.uuid4().hex}"
    lease = cloud_client.acquire_refresh_lease(
        account_id,
        lease_id=actual_lease_id,
        duration_seconds=lease_duration_seconds,
    )
    if not lease.acquired:
        raise RefreshLeaseUnavailable(f"baidu refresh lease is held by {lease.holder_device_id or 'another device'}")

    encrypted = cloud_client.get_token(account_id)
    if encrypted.encryption_method != BAIDU_ENCRYPTION_PASSWORD:
        raise ValueError("refresh flow currently requires password_argon2id_aes256gcm_v1 token encryption")

    current_plain = decrypt_token_envelope(
        encrypted.encrypted_token_json,
        encryption_method=encrypted.encryption_method,
        password_wrapping_key=password_wrapping_key,
    )
    refreshed = oauth_client.refresh_access_token(current_plain.refresh_token)
    refreshed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expires_at = refreshed_at + timedelta(seconds=refreshed.expires_in)
    updated_plain = PlainBaiduToken(
        access_token=refreshed.access_token,
        refresh_token=refreshed.refresh_token,
        token_type=refreshed.token_type or current_plain.token_type,
        scope=refreshed.scope or current_plain.scope,
        expires_at=expires_at,
    )
    encrypted_json = encrypt_password_token(updated_plain, password_wrapping_key)
    return cloud_client.update_token(
        account_id,
        expected_token_version=encrypted.token_version,
        token_expires_at=expires_at,
        encryption_method=encrypted.encryption_method,
        encrypted_token_json=encrypted_json,
        private_key_hint=encrypted.private_key_hint,
        last_verify_status="valid",
    )
