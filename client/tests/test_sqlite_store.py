from __future__ import annotations

import sqlite3
import uuid

from auto_backup_client.sqlite_store import SQLiteClientStore, build_version_fields, canonical_record_sha256, new_revision_id


def test_sqlite_migrations_are_idempotent_and_enable_upload_tables(tmp_path) -> None:
    db_path = tmp_path / "backup_state.sqlite3"
    store = SQLiteClientStore(db_path)

    store.migrate()
    store.migrate()

    with store.connect() as conn:
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]

    assert {
        "sync_outbox",
        "upload_sessions",
        "upload_parts",
        "remote_objects",
        "content_objects",
        "content_references",
        "schema_migrations",
    } <= tables
    assert foreign_keys == 1


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
            "remote_archive_path": "/apps/app/backups/2026/06/06/device-1/job-1/archives/000001-" + ("a" * 64) + ".7z",
            "remote_meta_path": "/apps/app/backups/2026/06/06/device-1/job-1/archives/000001-" + ("a" * 64) + ".meta.json",
            "remote_job_index_path": "/apps/app/backups/2026/06/06/device-1/job-1/job.index.json",
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
            "created_at": "2026-06-06T00:00:00Z",
        },
        updated_by_device_id="device-1",
        now="2026-06-06T00:00:00Z",
        revision_id="rev-1",
    )


def test_upload_session_and_outbox_are_written_in_same_transaction(tmp_path) -> None:
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()

    with store.transaction() as conn:
        store.put_upload_session(conn, _sample_upload_session_payload())

    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM upload_sessions").fetchone()[0] == 1
        outbox = conn.execute("SELECT * FROM sync_outbox").fetchone()

    assert outbox["payload_json"]
    assert "C:/sensitive/archive.7z" not in outbox["payload_json"]
    assert "uploadid-1" not in outbox["payload_json"]


def test_failed_transaction_rolls_back_business_and_outbox(tmp_path) -> None:
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    try:
        with store.transaction() as conn:
            store.put_upload_session(conn, _sample_upload_session_payload())
            raise sqlite3.IntegrityError("force rollback")
    except sqlite3.IntegrityError:
        pass

    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM upload_sessions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0] == 0


def test_successful_write_enqueues_outbox_payload_with_canonical_hash(tmp_path) -> None:
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
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
            "size_bytes": 1,
            "md5": "b" * 32,
            "sha256": "a" * 64,
            "fs_id": 123,
            "status": "remote_created",
            "created_at": "2026-06-06T00:00:00Z",
        },
        updated_by_device_id="device-1",
        now="2026-06-06T00:00:00Z",
        revision_id="rev-1",
    )

    with store.transaction() as conn:
        store.put_remote_object(conn, payload)

    with store.connect() as conn:
        remote = conn.execute("SELECT * FROM remote_objects").fetchone()
        outbox = conn.execute("SELECT * FROM sync_outbox").fetchone()

    assert len(remote["canonical_record_sha256"]) == 64
    assert outbox["entity_type"] == "remote_objects"
    assert outbox["revision_id"] == "rev-1"
    assert '"remote_path":"/apps/app/backups/file.7z"' in outbox["payload_json"]


def test_canonical_record_hash_ignores_revision_and_local_only_fields() -> None:
    base = {
        "entity_id": "upload_session_1",
        "job_id": "job-1",
        "remote_archive_path": "/apps/app/backups/file.7z",
        "archive_sha256": "a" * 64,
        "data_version": 1,
        "schema_version": 1,
        "revision_id": "rev-1",
        "updated_at": "2026-06-06T00:00:00Z",
        "updated_by_device_id": "device-1",
        "sync_status": "sync_pending",
        "local_archive_path": "C:/sensitive/archive.7z",
        "uploadid": "uploadid-1",
        "error_message": "contains local path C:/sensitive/archive.7z",
    }
    changed_control_fields = {
        **base,
        "schema_version": 2,
        "data_version": 2,
        "revision_id": "rev-2",
        "updated_at": "2026-06-06T01:00:00Z",
        "updated_by_device_id": "device-2",
        "sync_status": "syncing",
        "local_archive_path": "D:/other/archive.7z",
        "uploadid": "uploadid-2",
        "error_message": "different transient error",
    }
    changed_business_field = {**base, "remote_archive_path": "/apps/app/backups/other.7z"}

    assert canonical_record_sha256(base) == canonical_record_sha256(changed_control_fields)
    assert canonical_record_sha256(base) != canonical_record_sha256(changed_business_field)


def test_default_revision_id_is_uuid7() -> None:
    payload = build_version_fields(
        entity_payload={
            "entity_id": "entity-1",
            "job_id": "job-1",
        },
        updated_by_device_id="device-1",
    )
    parsed = uuid.UUID(str(payload["revision_id"]))

    assert parsed.version == 7
    assert parsed.variant == uuid.RFC_4122
    assert uuid.UUID(new_revision_id()).version == 7
