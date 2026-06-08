from __future__ import annotations

from pathlib import Path

from auto_backup_client.backup_jobs import BackupJobManager, BackupSourceInput
from auto_backup_client.backup_pipeline import BackupPipeline, BackupPipelineOptions
from auto_backup_client.restore_flow import RestoreFlowError, RestoreService
from auto_backup_client.sqlite_store import SQLiteClientStore
from test_backup_pipeline import FakeBaiduForPipeline, FakeCloudForPipeline


TEST_PASSWORD = "Test123456789"
NOW = "2026-06-08T21:00:00Z"


def test_restore_lists_cleaned_source_as_restorable_when_archive_is_local(tmp_path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("payload", encoding="utf-8")
    store, job_id = _completed_job(tmp_path, source)
    _mark_source_cleaned(store, job_id)

    report = RestoreService(store, device_id="device-1", cache_root=tmp_path / "cache").list_candidates(backup_job_id=job_id)

    assert report.restorable_count == 1
    assert report.local_ready_count == 1
    candidate = report.candidates[0]
    assert candidate.cleanup_status == "cleaned"
    assert candidate.candidate_status == "ready_local"


def test_restore_to_manual_path_writes_file_record_outbox_and_status(tmp_path) -> None:
    source = tmp_path / "private" / "source.txt"
    source.parent.mkdir()
    source.write_text("payload", encoding="utf-8")
    store, job_id = _completed_job(tmp_path, source)
    target_root = tmp_path / "restored"

    result = RestoreService(store, device_id="device-1", cache_root=tmp_path / "cache").restore(
        backup_job_id=job_id,
        target_mode="manual_path",
        target_root=target_root,
        password=TEST_PASSWORD,
        now=NOW,
    )

    restored = target_root / "source.txt"
    records = store.list_restore_records(job_id)
    references = store.list_content_references(job_id)
    with store.connect() as conn:
        outbox_payload = conn.execute(
            """
            SELECT payload_json
            FROM sync_outbox
            WHERE entity_type = 'restore_records'
            """
        ).fetchone()["payload_json"]

    assert result.restored_count == 1
    assert result.failed_count == 0
    assert restored.read_text(encoding="utf-8") == "payload"
    assert records[0]["restore_status"] == "restored"
    assert records[0]["archive_source"] == "local_cache"
    assert references[0]["restore_status"] == "restored"
    assert str(restored) not in outbox_payload
    assert str(source) not in outbox_payload
    assert str(tmp_path) not in outbox_payload


def test_restore_keep_both_does_not_overwrite_existing_file(tmp_path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("payload", encoding="utf-8")
    store, job_id = _completed_job(tmp_path, source)
    target_root = tmp_path / "restored"
    target_root.mkdir()
    existing = target_root / "source.txt"
    existing.write_text("existing", encoding="utf-8")

    result = RestoreService(store, device_id="device-1", cache_root=tmp_path / "cache").restore(
        backup_job_id=job_id,
        target_mode="manual_path",
        target_root=target_root,
        password=TEST_PASSWORD,
        now=NOW,
    )

    alternatives = sorted(path.name for path in target_root.iterdir())
    assert result.restored_count == 1
    assert existing.read_text(encoding="utf-8") == "existing"
    assert alternatives == ["source restored 20260608-210000.txt", "source.txt"]
    assert (target_root / "source restored 20260608-210000.txt").read_text(encoding="utf-8") == "payload"


def test_restore_wrong_password_records_failure_without_target_write(tmp_path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("payload", encoding="utf-8")
    store, job_id = _completed_job(tmp_path, source)

    result = RestoreService(store, device_id="device-1", cache_root=tmp_path / "cache").restore(
        backup_job_id=job_id,
        target_mode="manual_path",
        target_root=tmp_path / "restored",
        password="wrong-password",
        now=NOW,
    )

    records = store.list_restore_records(job_id)
    assert result.failed_count == 1
    assert not (tmp_path / "restored" / "source.txt").exists()
    assert records[0]["restore_status"] == "failed"
    assert records[0]["error_code"] == "archive_password_or_extract_failed"
    assert store.list_content_references(job_id)[0]["restore_status"] == "restore_failed"


def test_restore_requires_downloader_when_local_archive_missing(tmp_path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("payload", encoding="utf-8")
    store, job_id = _completed_job(tmp_path, source)
    archive = Path(store.list_archives(job_id)[0]["local_archive_path"])
    archive.unlink()

    report = RestoreService(store, device_id="device-1", cache_root=tmp_path / "cache").list_candidates(backup_job_id=job_id)
    assert report.needs_download_count == 1

    result = RestoreService(store, device_id="device-1", cache_root=tmp_path / "cache").restore(
        backup_job_id=job_id,
        target_mode="manual_path",
        target_root=tmp_path / "restored",
        password=TEST_PASSWORD,
        now=NOW,
    )

    assert result.failed_count == 1
    assert store.list_restore_records(job_id)[0]["error_code"] == "archive_unavailable"


def _completed_job(tmp_path, source: Path) -> tuple[SQLiteClientStore, str]:
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    created = BackupJobManager(store, device_id="device-1").create_job(
        [BackupSourceInput(str(source), "file")],
        now="2026-06-08T20:30:00Z",
    )
    job_id = created.job.backup_job_id
    result = BackupPipeline(
        store=store,
        device_id="device-1",
        baidu_client=FakeBaiduForPipeline(),
        cloud_client=FakeCloudForPipeline(),
    ).run_job(
        job_id,
        BackupPipelineOptions(
            cache_root=tmp_path / "cache",
            password=TEST_PASSWORD,
            account_id="account-1",
            run_upload=True,
            sync_outbox=True,
            reconcile_remote=True,
            now="2026-06-08T20:35:00Z",
        ),
    )
    assert result.completed is True
    return store, job_id


def _mark_source_cleaned(store: SQLiteClientStore, job_id: str) -> None:
    reference = store.list_content_references(job_id)[0]
    with store.transaction() as conn:
        store.update_content_reference_cleanup_status(
            conn,
            content_reference_id=str(reference["content_reference_id"]),
            cleanup_status="cleaned",
            updated_at=NOW,
            updated_by_device_id="device-1",
        )
