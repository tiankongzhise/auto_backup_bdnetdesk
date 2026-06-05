from __future__ import annotations

import httpx

from auto_backup_client.baidu.cloud_api import BaiduCloudClient, CloudAPIError


def test_cloud_client_sends_device_bearer_token_and_parses_accounts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer fake-device-token"
        assert request.url.path == "/v1/baidu/accounts"
        return httpx.Response(
            200,
            json={
                "accounts": [
                    {
                        "account_id": "bacc_1",
                        "display_name": "test account",
                        "baidu_uid": "uid-1",
                        "scope": "basic,netdisk",
                        "token_expires_at": "2026-06-05T08:00:00Z",
                        "token_valid": True,
                        "encryption_method": "password_argon2id_aes256gcm_v1",
                        "token_version": 3,
                        "selected": True,
                        "last_verify_status": "valid",
                    }
                ]
            },
        )

    cloud = BaiduCloudClient(
        "https://backup.baichengedu.com",
        "fake-device-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    accounts = cloud.list_accounts()

    assert accounts[0].account_id == "bacc_1"
    assert accounts[0].selected is True
    assert accounts[0].token_version == 3


def test_cloud_client_raises_structured_api_error() -> None:
    cloud = BaiduCloudClient(
        "https://backup.baichengedu.com",
        "fake-device-token",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(409, json={"error": "token_version_conflict", "message": "changed"})
            )
        ),
    )

    try:
        cloud.get_token("bacc_1")
    except CloudAPIError as exc:
        assert exc.status_code == 409
        assert exc.error_code == "token_version_conflict"
    else:
        raise AssertionError("expected CloudAPIError")
