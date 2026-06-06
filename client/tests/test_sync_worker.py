from __future__ import annotations

import sqlite3

from auto_backup_client.baidu.cloud_api import CloudAPIError
from auto_backup_client.baidu.models import SyncRevisionResult
from auto_backup_client.sqlite_store import SQLiteClientStore, build_version_fields
from auto_backup_client.sync_worker import SyncOutboxWorker


NOW = "2026-06-06T00:00:00Z"


class FakeCloudSyncClient:
    def __init__(self, results: list[SyncRevisionResult] | None = None, exc: Exception | None = None) -> None:
        self.results = results or []
        self.exc = exc
        self.sent_batches = []

    def sync_revisions(self, events):
        self.sent_batches.append(tuple(events))
        if self.exc is not None:
            raise self.exc
        return self.results


def test_worker_syncs_pending_events_and_marks_business_rows_synced(tmp_path) -> None:
    store = _store_with_session(tmp_path)
    event = store.list_outbox_events_for_sync(now=NOW)[0]
    cloud = FakeCloudSyncClient(
        [
            SyncRevisionResult(
                event_id=event.event_id,
                entity_id=event.entity_id,
                revision_id=event.revision_id,
                status="synced",
                cloud_data_version=1,
            )
        ]
    )

    result = SyncOutboxWorker(store=store, cloud=cloud).run_once(now=NOW)

    assert result.synced == 1
    assert cloud.sent_batches[0][0].event_id == event.event_id
    assert cloud.sent_batches[0][0].payload["job_id"] == "job-1"

    with store.connect() as conn:
        outbox = conn.execute("SELECT status, last_error FROM sync_outbox WHERE event_id = ?", (event.event_id,)).fetchone()
        session = conn.execute("SELECT sync_status, last_synced_revision_id FROM upload_sessions WHERE entity_id = ?", (event.entity_id,)).fetchone()

    assert outbox["status"] == "synced"
    assert outbox["last_error"] is None
    assert session["sync_status"] == "synced"
    assert session["last_synced_revision_id"] == event.revision_id


def test_worker_treats_duplicate_as_success(tmp_path) -> None:
    store = _store_with_session(tmp_path)
    event = store.list_outbox_events_for_sync(now=NOW)[0]
    cloud = FakeCloudSyncClient(
        [
            SyncRevisionResult(
                event_id=event.event_id,
                entity_id=event.entity_id,
                revision_id=event.revision_id,
                status="duplicate",
                cloud_data_version=1,
                cloud_revision_id=event.revision_id,
            )
        ]
    )

    result = SyncOutboxWorker(store=store, cloud=cloud).run_once(now=NOW)

    assert result.synced == 1
    with store.connect() as conn:
        outbox = conn.execute("SELECT status FROM sync_outbox WHERE event_id = ?", (event.event_id,)).fetchone()
        session = conn.execute("SELECT sync_status, last_synced_revision_id FROM upload_sessions WHERE entity_id = ?", (event.entity_id,)).fetchone()

    assert outbox["status"] == "synced"
    assert session["sync_status"] == "synced"
    assert session["last_synced_revision_id"] == event.revision_id


def test_worker_marks_conflict_on_business_and_outbox(tmp_path) -> None:
    store = _store_with_session(tmp_path)
    event = store.list_outbox_events_for_sync(now=NOW)[0]
    cloud = FakeCloudSyncClient(
        [
            SyncRevisionResult(
                event_id=event.event_id,
                entity_id=event.entity_id,
                revision_id=event.revision_id,
                status="conflict",
                reason="cloud has newer revision",
            )
        ]
    )

    result = SyncOutboxWorker(store=store, cloud=cloud).run_once(now=NOW)

    assert result.conflicts == 1
    with store.connect() as conn:
        outbox = conn.execute("SELECT status, last_error FROM sync_outbox WHERE event_id = ?", (event.event_id,)).fetchone()
        session = conn.execute("SELECT sync_status FROM upload_sessions WHERE entity_id = ?", (event.entity_id,)).fetchone()

    assert outbox["status"] == "sync_conflict"
    assert "newer" in outbox["last_error"]
    assert session["sync_status"] == "sync_conflict"


def test_worker_marks_rejected_terminal_without_retry(tmp_path) -> None:
    store = _store_with_session(tmp_path)
    event = store.list_outbox_events_for_sync(now=NOW)[0]
    cloud = FakeCloudSyncClient(
        [
            SyncRevisionResult(
                event_id=event.event_id,
                entity_id=event.entity_id,
                revision_id=event.revision_id,
                status="rejected",
                reason="payload missing field",
            )
        ]
    )

    result = SyncOutboxWorker(store=store, cloud=cloud).run_once(now=NOW)

    assert result.rejected == 1
    with store.connect() as conn:
        outbox = conn.execute("SELECT status, retry_count, next_retry_at, last_error FROM sync_outbox WHERE event_id = ?", (event.event_id,)).fetchone()

    assert outbox["status"] == "failed_terminal"
    assert outbox["retry_count"] == 0
    assert outbox["next_retry_at"] is None
    assert "payload" in outbox["last_error"]


def test_worker_retries_all_selected_events_when_cloud_is_unavailable(tmp_path) -> None:
    store = _store_with_session(tmp_path)
    event = store.list_outbox_events_for_sync(now=NOW)[0]
    cloud = FakeCloudSyncClient(exc=CloudAPIError(503, "retryable_error", "cloud sync store is unavailable"))

    result = SyncOutboxWorker(store=store, cloud=cloud).run_once(now=NOW)

    assert result.retryable == 1
    with store.connect() as conn:
        outbox = conn.execute("SELECT status, retry_count, next_retry_at, last_error FROM sync_outbox WHERE event_id = ?", (event.event_id,)).fetchone()
        session = conn.execute("SELECT sync_status FROM upload_sessions WHERE entity_id = ?", (event.entity_id,)).fetchone()

    assert outbox["status"] == "retryable"
    assert outbox["retry_count"] == 1
    assert outbox["next_retry_at"] == "2026-06-06T00:00:02Z"
    assert outbox["last_error"] == "503:retryable_error"
    assert session["sync_status"] == "sync_failed_retryable"


def test_worker_filters_retryable_until_next_retry_time(tmp_path) -> None:
    store = _store_with_session(tmp_path)
    first = store.list_outbox_events_for_sync(now=NOW)[0]
    store.mark_outbox_retryable((first.event_id,), reason="temporary", now=NOW)
    assert store.list_outbox_events_for_sync(now="2026-06-06T00:00:01Z") == []

    events = store.list_outbox_events_for_sync(now="2026-06-06T00:00:03Z")

    assert [event.event_id for event in events] == [first.event_id]


def test_worker_caps_batch_at_100_events(tmp_path) -> None:
    store = _store_with_session(tmp_path)
    for index in range(105):
        _insert_remote_object(store, remote_object_id=f"remote-{index}", entity_id=f"remote_entity_{index}")

    all_events = store.list_outbox_events_for_sync(limit=100, now=NOW)
    cloud = FakeCloudSyncClient(
        [
            SyncRevisionResult(
                event_id=event.event_id,
                entity_id=event.entity_id,
                revision_id=event.revision_id,
                status="synced",
            )
            for event in all_events
        ]
    )

    result = SyncOutboxWorker(store=store, cloud=cloud).run_once(now=NOW)

    assert result.selected == 100
    assert result.sent == 100
    assert len(cloud.sent_batches[0]) == 100
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sync_outbox WHERE status = 'pending'").fetchone()[0] == 6
        assert conn.execute("SELECT COUNT(*) FROM sync_outbox WHERE status = 'synced'").fetchone()[0] == 100


def _store_with_session(tmp_path) -> SQLiteClientStore:
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    payload = _sample_upload_session_payload()
    with store.transaction() as conn:
        store.put_upload_session(conn, payload)
    return store


def _sample_upload_session_payload() -> dict[str, object]:
    return build_version_fields(
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
            "archive_size": 1,
            "archive_type": "payload",
            "local_archive_path": "C:/sensitive/archive.7z",
            "remote_archive_path": "/apps/app/backups/file.7z",
            "remote_meta_path": "/apps/app/backups/file.meta.json",
            "remote_job_index_path": "/apps/app/backups/job.index.json",
            "part_size": 4 * 1024 * 1024,
            "total_parts": 1,
            "block_md5s_json": '["' + ("b" * 32) + '"]',
            "uploadid": "uploadid-1",
            "upload_status": "precreated",
            "meta_status": "pending",
            "job_index_status": "pending",
            "fs_id": 0,
            "remote_md5": "",
            "error_code": "",
            "error_message": "",
            "completed_at": None,
            "created_at": NOW,
        },
        updated_by_device_id="device-1",
        now=NOW,
    )


def _insert_remote_object(store: SQLiteClientStore, *, remote_object_id: str, entity_id: str) -> None:
    payload = build_version_fields(
        entity_payload={
            "remote_object_id": remote_object_id,
            "entity_id": entity_id,
            "object_type": "archive",
            "job_id": "job-1",
            "device_id": "device-1",
            "archive_id": "archive-1",
            "archive_sha256": "a" * 64,
            "remote_path": f"/apps/app/backups/{remote_object_id}.7z",
            "size_bytes": 1,
            "md5": "b" * 32,
            "sha256": "a" * 64,
            "fs_id": 123,
            "status": "remote_created",
            "created_at": NOW,
        },
        updated_by_device_id="device-1",
        now=NOW,
    )
    try:
        with store.transaction() as conn:
            store.put_remote_object(conn, payload)
    except sqlite3.IntegrityError as exc:
        raise AssertionError("test generated duplicate remote object") from exc
