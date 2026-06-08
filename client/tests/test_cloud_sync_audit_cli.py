from __future__ import annotations

import json

import httpx

from auto_backup_client.cloud_sync_audit_cli import main
from auto_backup_client.device_credentials import DeviceCredentialStoreError


def test_cloud_sync_audit_posts_probe_and_verifies_duplicate(capsys, monkeypatch) -> None:
    monkeypatch.setenv("CLOUD_API_DEVICE_TOKEN", "secret-device-token")
    seen_events: list[dict[str, object]] = []
    original_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "backup.baichengedu.com"
        if request.url.path == "/v1/readyz":
            assert "Authorization" not in request.headers
            return httpx.Response(200, json={"status": "ready"})

        assert request.headers["Authorization"] == "Bearer secret-device-token"
        if request.url.path == "/v1/sync/revisions":
            body = json.loads(request.content.decode("utf-8"))
            event = body["events"][0]
            assert event["entity_type"] == "release_sync_audits"
            assert event["payload"]["purpose"] == "p3_14_cloud_sync_truth_probe"
            assert event["payload"]["probe_label_sha256"]
            assert "secret label" not in json.dumps(event, ensure_ascii=False)
            seen_events.append(event)
            status = "synced" if len(seen_events) == 1 else "duplicate"
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "event_id": event["event_id"],
                            "entity_id": event["entity_id"],
                            "revision_id": event["revision_id"],
                            "status": status,
                            "cloud_data_version": event["data_version"],
                        }
                    ]
                },
            )

        if request.url.path.startswith("/v1/reconcile/entities/"):
            assert seen_events
            event = seen_events[0]
            return httpx.Response(
                200,
                json={
                    "entity_id": event["entity_id"],
                    "entity_type": event["entity_type"],
                    "data_version": event["data_version"],
                    "revision_id": event["revision_id"],
                    "canonical_record_sha256": event["canonical_record_sha256"],
                    "updated_by_device_id": "environment",
                    "recent_revisions": [
                        {
                            "event_id": event["event_id"],
                            "revision_id": event["revision_id"],
                            "data_version": event["data_version"],
                            "apply_status": "synced",
                            "canonical_record_sha256": event["canonical_record_sha256"],
                            "created_at": event["updated_at"],
                        }
                    ],
                },
            )

        return httpx.Response(404, json={"error": "not_found", "message": "missing"})

    monkeypatch.setattr(httpx, "Client", lambda *args, **kwargs: original_client(transport=httpx.MockTransport(handler)))

    assert main(["--probe-label", "secret label"]) == 0
    output = capsys.readouterr().out

    assert "first_sync_status: synced" in output
    assert "summary_matched: true" in output
    assert "duplicate_sync_status: duplicate" in output
    assert "duplicate_verified: true" in output
    assert "cloud_sync_truthful: true" in output
    assert "secret-device-token" not in output
    assert "secret label" not in output
    assert len(seen_events) == 2
    assert seen_events[0] == seen_events[1]


def test_cloud_sync_audit_fails_when_readyz_is_not_ready(capsys, monkeypatch) -> None:
    monkeypatch.setenv("CLOUD_API_DEVICE_TOKEN", "secret-device-token")
    original_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/readyz":
            return httpx.Response(503, json={"error": "schema_not_ready"})
        raise AssertionError("sync should not run when readyz fails")

    monkeypatch.setattr(httpx, "Client", lambda *args, **kwargs: original_client(transport=httpx.MockTransport(handler)))

    assert main([]) == 1
    output = capsys.readouterr().out

    assert "操作失败: cloud_api_error status=503 code=not_ready" in output
    assert "secret-device-token" not in output


def test_cloud_sync_audit_redacts_device_credential_store_errors(capsys, monkeypatch) -> None:
    def fail_credentials(**_kwargs: object) -> None:
        raise DeviceCredentialStoreError("C:/Users/3700x/AppData/Local/secret-device-credentials.json")

    monkeypatch.setattr("auto_backup_client.cloud_sync_audit_cli.resolve_or_register_device_credentials", fail_credentials)

    assert main([]) == 1
    output = capsys.readouterr().out

    assert "操作失败: device_credential_store_error" in output
    assert "C:/Users/3700x" not in output
    assert "secret-device-credentials" not in output
