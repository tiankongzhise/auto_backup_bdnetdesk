from __future__ import annotations

import base64
from datetime import datetime
from typing import Any

import httpx

from auto_backup_client.baidu.models import (
    BaiduAccount,
    BaiduAuthSession,
    BaiduEncryptedToken,
    BaiduRefreshLease,
    CompleteAuthResult,
    DeviceRegistration,
    SyncRevisionEvent,
    SyncRevisionResult,
    format_datetime,
)


class CloudAPIError(RuntimeError):
    def __init__(self, status_code: int, error_code: str, message: str) -> None:
        super().__init__(f"cloud api returned {status_code} {error_code}: {message}")
        self.status_code = status_code
        self.error_code = error_code
        self.message = message


class BaiduCloudClient:
    def __init__(
        self,
        base_url: str,
        device_token: str,
        *,
        http_client: httpx.Client | None = None,
        timeout: float = 20.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._device_token = device_token
        self._client = http_client or httpx.Client(timeout=timeout)
        self._owns_client = http_client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "BaiduCloudClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def list_accounts(self) -> list[BaiduAccount]:
        data = self._request("GET", "/v1/baidu/accounts")
        return [BaiduAccount.from_json(item) for item in data.get("accounts", [])]

    def select_account(self, account_id: str) -> BaiduAccount:
        data = self._request("POST", f"/v1/baidu/accounts/{account_id}/select", json={})
        return BaiduAccount.from_json(data)

    def create_auth_session(
        self,
        *,
        flow: str = "device_code",
        encryption_method: str = "password_argon2id_aes256gcm_v1",
        rsa_public_key_pem: str = "",
        private_key_hint: str = "",
    ) -> BaiduAuthSession:
        payload: dict[str, Any] = {
            "flow": flow,
            "encryption_method": encryption_method,
        }
        if rsa_public_key_pem:
            payload["rsa_public_key_pem"] = rsa_public_key_pem
        if private_key_hint:
            payload["private_key_hint"] = private_key_hint
        data = self._request("POST", "/v1/baidu/auth/sessions", json=payload)
        return BaiduAuthSession.from_json(data)

    def get_auth_session(self, session_id: str) -> BaiduAuthSession:
        data = self._request("GET", f"/v1/baidu/auth/sessions/{session_id}")
        return BaiduAuthSession.from_json(data)

    def complete_auth_session(
        self,
        session_id: str,
        *,
        wrapping_key: bytes | None = None,
        rsa_public_key_pem: str = "",
        private_key_hint: str = "",
    ) -> CompleteAuthResult:
        payload: dict[str, Any] = {}
        if wrapping_key is not None:
            payload["wrapping_key_base64"] = _b64url_encode(wrapping_key)
        if rsa_public_key_pem:
            payload["rsa_public_key_pem"] = rsa_public_key_pem
        if private_key_hint:
            payload["private_key_hint"] = private_key_hint
        data = self._request("POST", f"/v1/baidu/auth/sessions/{session_id}/complete", json=payload)
        return CompleteAuthResult.from_json(data)

    def get_token(self, account_id: str) -> BaiduEncryptedToken:
        data = self._request("GET", f"/v1/baidu/accounts/{account_id}/token")
        return BaiduEncryptedToken.from_json(data)

    def update_token(
        self,
        account_id: str,
        *,
        expected_token_version: int,
        token_expires_at: datetime,
        encryption_method: str,
        encrypted_token_json: dict[str, Any],
        private_key_hint: str = "",
        last_verify_status: str = "valid",
    ) -> BaiduEncryptedToken:
        payload: dict[str, Any] = {
            "expected_token_version": expected_token_version,
            "token_expires_at": format_datetime(token_expires_at),
            "encryption_method": encryption_method,
            "encrypted_token_json": encrypted_token_json,
            "last_verify_status": last_verify_status,
        }
        if private_key_hint:
            payload["private_key_hint"] = private_key_hint
        data = self._request("PUT", f"/v1/baidu/accounts/{account_id}/token", json=payload)
        return BaiduEncryptedToken.from_json(data)

    def acquire_refresh_lease(
        self,
        account_id: str,
        *,
        lease_id: str = "",
        duration_seconds: int = 300,
    ) -> BaiduRefreshLease:
        payload: dict[str, Any] = {"duration_seconds": duration_seconds}
        if lease_id:
            payload["lease_id"] = lease_id
        data = self._request(
            "POST",
            f"/v1/baidu/accounts/{account_id}/refresh-lease",
            expected_error_statuses={409},
            json=payload,
        )
        return BaiduRefreshLease.from_json(data)

    def sync_revisions(self, events: list[SyncRevisionEvent] | tuple[SyncRevisionEvent, ...]) -> list[SyncRevisionResult]:
        if not events:
            raise ValueError("events must not be empty")
        if len(events) > 100:
            raise ValueError("at most 100 sync revision events are accepted per request")
        data = self._request(
            "POST",
            "/v1/sync/revisions",
            json={"events": [event.to_json() for event in events]},
        )
        raw_results = data.get("results", [])
        if not isinstance(raw_results, list):
            raise CloudAPIError(200, "invalid_response", "results must be a list")
        return [SyncRevisionResult.from_json(item) for item in raw_results]

    def _request(
        self,
        method: str,
        path: str,
        *,
        expected_error_statuses: set[int] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        response = self._client.request(
            method,
            self._base_url + path,
            headers={"Authorization": f"Bearer {self._device_token}"},
            **kwargs,
        )
        if response.status_code >= 400 and response.status_code not in (expected_error_statuses or set()):
            self._raise_api_error(response)
        data = response.json()
        if not isinstance(data, dict):
            raise CloudAPIError(response.status_code, "invalid_response", "response body must be a JSON object")
        return data

    @staticmethod
    def _raise_api_error(response: httpx.Response) -> None:
        try:
            data = response.json()
        except ValueError:
            raise CloudAPIError(response.status_code, "http_error", response.text) from None
        error_code = str(data.get("error", "http_error")) if isinstance(data, dict) else "http_error"
        message = str(data.get("message", "")) if isinstance(data, dict) else response.text
        raise CloudAPIError(response.status_code, error_code, message)


def register_device(
    base_url: str,
    *,
    device_name: str,
    hostname: str = "",
    os_version: str = "",
    client_version: str = "",
    http_client: httpx.Client | None = None,
    timeout: float = 20.0,
) -> DeviceRegistration:
    client = http_client or httpx.Client(timeout=timeout)
    owns_client = http_client is None
    try:
        response = client.post(
            base_url.rstrip("/") + "/v1/devices/register",
            json={
                "device_name": device_name,
                "hostname": hostname,
                "os_version": os_version,
                "client_version": client_version,
            },
        )
        if response.status_code >= 400:
            BaiduCloudClient._raise_api_error(response)
        data = response.json()
        if not isinstance(data, dict):
            raise CloudAPIError(response.status_code, "invalid_response", "response body must be a JSON object")
        return DeviceRegistration.from_json(data)
    finally:
        if owns_client:
            client.close()


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")
