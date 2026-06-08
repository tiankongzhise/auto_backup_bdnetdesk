from __future__ import annotations

import httpx

from auto_backup_client.baidu.cloud_api import BaiduCloudClient, CloudAPIError
from auto_backup_client.baidu.models import SyncRevisionEvent


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


def test_refresh_lease_conflict_is_parsed_as_business_result() -> None:
    cloud = BaiduCloudClient(
        "https://backup.baichengedu.com",
        "fake-device-token",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    409,
                    json={
                        "acquired": False,
                        "account_id": "bacc_1",
                        "lease_id": "lease-first",
                        "holder_device_id": "dev_first",
                        "expires_at": "2026-06-05T08:05:00Z",
                    },
                )
            )
        ),
    )

    lease = cloud.acquire_refresh_lease("bacc_1", lease_id="lease-second")

    assert lease.acquired is False
    assert lease.lease_id == "lease-first"
    assert lease.holder_device_id == "dev_first"


def test_sync_revisions_posts_events_and_parses_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer fake-device-token"
        assert request.method == "POST"
        assert request.url.path == "/v1/sync/revisions"
        payload = request.read()
        assert b'"event_id":"evt-1"' in payload
        assert b'"payload":{"remote_path":"/apps/app/file.7z"}' in payload
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "event_id": "evt-1",
                        "entity_id": "entity-1",
                        "revision_id": "01971234-5678-7abc-8def-0123456789ab",
                        "status": "synced",
                        "cloud_data_version": 1,
                    }
                ]
            },
        )

    cloud = BaiduCloudClient(
        "https://backup.baichengedu.com",
        "fake-device-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    results = cloud.sync_revisions(
        [
            SyncRevisionEvent(
                event_id="evt-1",
                entity_type="remote_objects",
                entity_id="entity-1",
                revision_id="01971234-5678-7abc-8def-0123456789ab",
                schema_version=1,
                data_version=1,
                operation="upsert",
                canonical_record_sha256="a" * 64,
                payload={"remote_path": "/apps/app/file.7z"},
                updated_at="2026-06-06T00:00:00Z",
            )
        ]
    )

    assert results[0].status == "synced"
    assert results[0].cloud_data_version == 1


def test_sync_revisions_raises_retryable_cloud_error() -> None:
    cloud = BaiduCloudClient(
        "https://backup.baichengedu.com",
        "fake-device-token",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(503, json={"error": "retryable_error", "message": "cloud sync store is unavailable"})
            )
        ),
    )

    try:
        cloud.sync_revisions(
            [
                SyncRevisionEvent(
                    event_id="evt-1",
                    entity_type="remote_objects",
                    entity_id="entity-1",
                    revision_id="01971234-5678-7abc-8def-0123456789ab",
                    schema_version=1,
                    data_version=1,
                    operation="upsert",
                    canonical_record_sha256="a" * 64,
                    payload={"remote_path": "/apps/app/file.7z"},
                    updated_at="2026-06-06T00:00:00Z",
                )
            ]
        )
    except CloudAPIError as exc:
        assert exc.status_code == 503
        assert exc.error_code == "retryable_error"
    else:
        raise AssertionError("expected CloudAPIError")


def test_get_entity_summary_parses_revision_projection() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer fake-device-token"
        assert request.method == "GET"
        assert request.url.path == "/v1/reconcile/entities/entity-1"
        return httpx.Response(
            200,
            json={
                "entity_id": "entity-1",
                "entity_type": "remote_objects",
                "data_version": 2,
                "revision_id": "01971234-5678-7abc-8def-0123456789ab",
                "canonical_record_sha256": "a" * 64,
                "updated_by_device_id": "dev-1",
                "recent_revisions": [
                    {
                        "event_id": "evt-1",
                        "revision_id": "01971234-5678-7abc-8def-0123456789ab",
                        "data_version": 2,
                        "apply_status": "synced",
                        "canonical_record_sha256": "a" * 64,
                        "created_at": "2026-06-06T00:00:00Z",
                    }
                ],
            },
        )

    cloud = BaiduCloudClient(
        "https://backup.baichengedu.com",
        "fake-device-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    summary = cloud.get_entity_summary("entity-1")

    assert summary.entity_type == "remote_objects"
    assert summary.data_version == 2
    assert summary.recent_revisions[0].apply_status == "synced"


def test_get_content_parses_cloud_dedupe_candidate() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer fake-device-token"
        assert request.method == "GET"
        assert request.url.path == "/v1/contents/" + ("c" * 64)
        return httpx.Response(
            200,
            json={
                "content_id": "c" * 64,
                "file_sha256": "a" * 64,
                "size_bytes": 123,
                "latest_entity_id": "content_object_entity",
                "updated_at": "2026-06-08T03:00:00Z",
            },
        )

    cloud = BaiduCloudClient(
        "https://backup.baichengedu.com",
        "fake-device-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    content = cloud.get_content("c" * 64)

    assert content.content_id == "c" * 64
    assert content.file_sha256 == "a" * 64
    assert content.size_bytes == 123
    assert content.latest_entity_id == "content_object_entity"
