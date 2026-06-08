from __future__ import annotations

import json

import pytest

from auto_backup_client.backup_jobs import BackupJobError, BackupJobManager, BackupSourceInput, status_label
from auto_backup_client.sqlite_store import SQLiteClientStore


def test_create_backup_job_persists_sources_and_sync_outbox_without_local_paths(tmp_path) -> None:
    source_file = tmp_path / "source.txt"
    source_file.write_text("hello", encoding="utf-8")
    source_dir = tmp_path / "photos"
    source_dir.mkdir()
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    manager = BackupJobManager(store, device_id="device-1")

    created = manager.create_job(
        [str(source_file), str(source_dir)],
        job_name="  family archive  ",
        now="2026-06-08T01:00:00Z",
    )

    assert created.job.job_name == "family archive"
    assert created.job.status == "queued"
    assert created.job.source_count == 2
    assert [source.source_type for source in created.sources] == ["file", "directory"]
    assert {source.display_name for source in created.sources} == {"source.txt", "photos"}

    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM backup_jobs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM backup_sources").fetchone()[0] == 2
        outbox = conn.execute("SELECT * FROM sync_outbox").fetchone()

    assert outbox["entity_type"] == "backup_jobs"
    payload = json.loads(outbox["payload_json"])
    assert payload["job_name"] == "family archive"
    assert str(source_file) not in outbox["payload_json"]
    assert str(source_dir) not in outbox["payload_json"]


def test_create_backup_job_rejects_empty_and_duplicate_sources(tmp_path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("hello", encoding="utf-8")
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    manager = BackupJobManager(store, device_id="device-1")

    with pytest.raises(BackupJobError):
        manager.create_job([])

    with pytest.raises(BackupJobError):
        manager.create_job([str(source), str(source)])

    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM backup_jobs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0] == 0


def test_backup_job_status_transitions_are_versioned(tmp_path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("hello", encoding="utf-8")
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    manager = BackupJobManager(store, device_id="device-1")
    created = manager.create_job([str(source)], now="2026-06-08T01:00:00Z")

    running = manager.transition_job(created.job.backup_job_id, "running", now="2026-06-08T01:01:00Z")
    paused = manager.transition_job(created.job.backup_job_id, "paused", now="2026-06-08T01:02:00Z")
    resumed = manager.transition_job(created.job.backup_job_id, "running", now="2026-06-08T01:03:00Z")
    canceled = manager.transition_job(created.job.backup_job_id, "canceled", now="2026-06-08T01:04:00Z")

    assert running.job.status == "running"
    assert paused.job.status == "paused"
    assert resumed.job.status == "running"
    assert canceled.job.status == "canceled"
    assert canceled.job.data_version == 5
    assert status_label("paused") == "已暂停"

    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0] == 5


def test_invalid_backup_job_status_transition_does_not_enqueue_revision(tmp_path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("hello", encoding="utf-8")
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    manager = BackupJobManager(store, device_id="device-1")
    created = manager.create_job([BackupSourceInput(str(source), "file")], now="2026-06-08T01:00:00Z")

    with pytest.raises(BackupJobError):
        manager.transition_job(created.job.backup_job_id, "completed", now="2026-06-08T01:01:00Z")

    with store.connect() as conn:
        job = conn.execute("SELECT status, data_version FROM backup_jobs").fetchone()
        assert job["status"] == "queued"
        assert job["data_version"] == 1
        assert conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0] == 1

