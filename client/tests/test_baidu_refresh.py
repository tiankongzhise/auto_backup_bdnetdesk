from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import parse_qs

import httpx

from auto_backup_client.baidu.cloud_api import BaiduCloudClient
from auto_backup_client.baidu.crypto import PlainBaiduToken, decrypt_token_envelope, encrypt_password_token
from auto_backup_client.baidu.refresh import BaiduOAuthTokenClient, refresh_baidu_account_token


def test_refresh_flow_acquires_lease_refreshes_and_updates_expected_version() -> None:
    key = bytes([9]) * 32
    current_plain = PlainBaiduToken(
        access_token="fake-old-access-token",
        refresh_token="fake-old-refresh-token",
        scope="basic,netdisk",
        expires_at=datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc),
    )
    current_envelope = encrypt_password_token(current_plain, key)
    requests: list[httpx.Request] = []
    update_payload: dict[str, object] = {}

    def cloud_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/baidu/accounts/bacc_1/refresh-lease":
            return httpx.Response(
                200,
                json={
                    "acquired": True,
                    "account_id": "bacc_1",
                    "lease_id": "lease-1",
                    "holder_device_id": "device-1",
                    "expires_at": "2026-06-05T08:05:00Z",
                },
            )
        if request.url.path == "/v1/baidu/accounts/bacc_1/token" and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "account_id": "bacc_1",
                    "encryption_method": "password_argon2id_aes256gcm_v1",
                    "token_version": 7,
                    "token_expires_at": "2026-06-05T08:00:00Z",
                    "encrypted_token_json": current_envelope,
                },
            )
        if request.url.path == "/v1/baidu/accounts/bacc_1/token" and request.method == "PUT":
            update_payload.update(json.loads(request.content.decode("utf-8")))
            return httpx.Response(
                200,
                json={
                    "account_id": "bacc_1",
                    "encryption_method": "password_argon2id_aes256gcm_v1",
                    "token_version": 8,
                    "token_expires_at": update_payload["token_expires_at"],
                    "encrypted_token_json": update_payload["encrypted_token_json"],
                },
            )
        return httpx.Response(404, json={"error": "not_found", "message": "missing route"})

    def oauth_handler(request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode("utf-8"))
        assert form["grant_type"] == ["refresh_token"]
        assert form["refresh_token"] == ["fake-old-refresh-token"]
        assert form["client_id"] == ["app-key"]
        assert form["client_secret"] == ["fake-app-secret"]
        return httpx.Response(
            200,
            json={
                "access_token": "fake-new-access-token",
                "refresh_token": "fake-new-refresh-token",
                "expires_in": 3600,
                "token_type": "Bearer",
                "scope": "basic,netdisk",
            },
        )

    cloud = BaiduCloudClient(
        "https://backup.baichengedu.com",
        "fake-device-token",
        http_client=httpx.Client(transport=httpx.MockTransport(cloud_handler)),
    )
    oauth = BaiduOAuthTokenClient(
        "https://openapi.baidu.com/oauth/2.0/token",
        "app-key",
        "fake-app-secret",
        http_client=httpx.Client(transport=httpx.MockTransport(oauth_handler)),
    )

    updated = refresh_baidu_account_token(
        cloud_client=cloud,
        oauth_client=oauth,
        account_id="bacc_1",
        password_wrapping_key=key,
        lease_id="lease-1",
        now=datetime(2026, 6, 5, 8, 30, tzinfo=timezone.utc),
    )

    assert [request.url.path for request in requests] == [
        "/v1/baidu/accounts/bacc_1/refresh-lease",
        "/v1/baidu/accounts/bacc_1/token",
        "/v1/baidu/accounts/bacc_1/token",
    ]
    assert update_payload["expected_token_version"] == 7
    assert "fake-new-access-token" not in str(update_payload["encrypted_token_json"])
    assert "fake-new-refresh-token" not in str(update_payload["encrypted_token_json"])
    decrypted = decrypt_token_envelope(
        updated.encrypted_token_json,
        encryption_method=updated.encryption_method,
        password_wrapping_key=key,
    )
    assert decrypted.access_token == "fake-new-access-token"
    assert decrypted.refresh_token == "fake-new-refresh-token"
