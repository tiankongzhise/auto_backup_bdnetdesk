from __future__ import annotations

import json

import httpx

from auto_backup_client.device_credentials import DeviceCredentialStore, DeviceCredentials
from auto_backup_client.sqlite_store import SQLiteClientStore, build_version_fields
from auto_backup_client.sync_cli import main


NOW = "2026-06-06T00:00:00Z"


def test_sync_outbox_cli_outputs_redacted_counts_and_verifies_summary(tmp_path, capsys, monkeypatch) -> None:
    db_path = tmp_path / "backup_state.sqlite3"
    store = SQLiteClientStore(db_path)
    store.migrate()
    payload = build_version_fields(
        entity_payload={
            "remote_object_id": "remote-1",
            "entity_id": "remote_object_remote-1",
            "object_type": "archive",
            "job_id": "job-1",
            "device_id": "device-1",
            "archive_id": "archive-1",
            "archive_sha256": "a" * 64,
            "remote_path": "/apps/app/backups/file.7z",
            "size_bytes": 10,
            "md5": "b" * 32,
            "sha256": "a" * 64,
            "fs_id": 123,
            "status": "remote_created",
            "created_at": NOW,
        },
        updated_by_device_id="device-1",
        now=NOW,
    )
    with store.transaction() as conn:
        store.put_remote_object(conn, payload)

    credential_store = DeviceCredentialStore(tmp_path / "device_credentials.json", allow_plaintext=True)
    credential_store.save(
        DeviceCredentials(
            cloud_api_base_url="https://backup.baichengedu.com",
            device_id="device-1",
            device_token="secret-device-token",
        )
    )
    monkeypatch.setenv("AUTO_BACKUP_DEVICE_CREDENTIAL_STORE_PATH", str(credential_store.path))
    monkeypatch.setenv("AUTO_BACKUP_DEVICE_CREDENTIAL_STORE_ALLOW_PLAINTEXT", "true")

    original_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer secret-device-token"
        if request.url.path == "/v1/sync/revisions":
            body = json.loads(request.content.decode("utf-8"))
            event = body["events"][0]
            assert event["payload"]["remote_path"] == "/apps/app/backups/file.7z"
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "event_id": event["event_id"],
                            "entity_id": event["entity_id"],
                            "revision_id": event["revision_id"],
                            "status": "synced",
                            "cloud_data_version": event["data_version"],
                        }
                    ]
                },
            )
        if request.url.path == "/v1/reconcile/entities/remote_object_remote-1":
            return httpx.Response(
                200,
                json={
                    "entity_id": payload["entity_id"],
                    "entity_type": "remote_objects",
                    "data_version": payload["data_version"],
                    "revision_id": payload["revision_id"],
                    "canonical_record_sha256": payload["canonical_record_sha256"],
                    "updated_by_device_id": "device-1",
                    "recent_revisions": [
                        {
                            "event_id": "evt-1",
                            "revision_id": payload["revision_id"],
                            "data_version": payload["data_version"],
                            "apply_status": "synced",
                            "canonical_record_sha256": payload["canonical_record_sha256"],
                            "created_at": NOW,
                        }
                    ],
                },
            )
        return httpx.Response(404, json={"error": "not_found", "message": "missing"})

    monkeypatch.setattr(httpx, "Client", lambda *args, **kwargs: original_client(transport=httpx.MockTransport(handler)))

    assert (
        main(
            [
                "sync-outbox",
                "--sqlite-path",
                str(db_path),
                "--verify-cloud-summary",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out

    assert "selected: 1" in output
    assert "sent: 1" in output
    assert "synced: 1" in output
    assert "cloud_summary_verified: 1" in output
    assert "secret-device-token" not in output
    assert str(db_path) not in output
    assert "/apps/app/backups/file.7z" not in output


def test_sync_outbox_cli_marks_retryable_on_cloud_503(tmp_path, capsys, monkeypatch) -> None:
    db_path = tmp_path / "backup_state.sqlite3"
    store = SQLiteClientStore(db_path)
    store.migrate()
    payload = build_version_fields(
        entity_payload={
            "upload_session_id": "upload-1",
            "entity_id": "upload_session_upload-1",
            "job_id": "job-1",
            "device_id": "device-1",
            "account_id": "account-1",
            "archive_id": "archive-1",
            "archive_seq": 1,
            "archive_sha256": "a" * 64,
            "archive_md5": "b" * 32,
            "archive_size": 10,
            "archive_type": "payload",
            "local_archive_path": "C:/secret/archive.7z",
            "remote_archive_path": "/apps/app/backups/file.7z",
            "remote_meta_path": "/apps/app/backups/file.meta.json",
            "remote_job_index_path": "/apps/app/backups/job.index.json",
            "part_size": 4 * 1024 * 1024,
            "total_parts": 1,
            "block_md5s_json": '["' + ("b" * 32) + '"]',
            "uploadid": "secret-uploadid",
            "upload_status": "remote_created",
            "meta_status": "uploaded",
            "job_index_status": "uploaded",
            "fs_id": 123,
            "remote_md5": "b" * 32,
            "error_code": "",
            "error_message": "",
            "completed_at": NOW,
            "created_at": NOW,
        },
        updated_by_device_id="device-1",
        now=NOW,
    )
    with store.transaction() as conn:
        store.put_upload_session(conn, payload)

    monkeypatch.setenv("CLOUD_API_DEVICE_TOKEN", "secret-device-token")
    monkeypatch.delenv("AUTO_BACKUP_DEVICE_CREDENTIAL_STORE_PATH", raising=False)
    monkeypatch.delenv("AUTO_BACKUP_DEVICE_CREDENTIAL_STORE_ALLOW_PLAINTEXT", raising=False)

    original_client = httpx.Client

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "retryable_error", "message": "cloud sync store is unavailable"})

    monkeypatch.setattr(httpx, "Client", lambda *args, **kwargs: original_client(transport=httpx.MockTransport(handler)))

    assert main(["sync-outbox", "--sqlite-path", str(db_path)]) == 0
    output = capsys.readouterr().out

    assert "retryable: 1" in output
    assert "secret-device-token" not in output
    assert "C:/secret/archive.7z" not in output
    assert str(db_path) not in output
    with store.connect() as conn:
        outbox = conn.execute("SELECT status, retry_count FROM sync_outbox").fetchone()
    assert outbox["status"] == "retryable"
    assert outbox["retry_count"] == 1


def test_sync_outbox_cli_failure_does_not_print_sqlite_path(tmp_path, capsys) -> None:
    db_path = tmp_path / "sensitive-state.sqlite3"

    assert main(["sync-outbox", "--sqlite-path", str(db_path), "--batch-size", "999"]) == 1
    output = capsys.readouterr().out

    assert "batch_size must be between 1 and 100" in output
    assert str(db_path) not in output
    assert "sensitive-state.sqlite3" not in output
