from __future__ import annotations

from pathlib import Path

import pytest

from auto_backup_client.backup_jobs import BackupJobManager, BackupSourceInput
from auto_backup_client.backup_pipeline import BackupPipeline, BackupPipelineOptions
from auto_backup_client.file_identity import FileIdentity
from auto_backup_client.source_cleanup import (
    CLEANUP_CONFIRM_TEXT,
    PERMANENT_DELETE_CONFIRM_TEXT,
    SourceCleanupError,
    SourceCleanupService,
)
from auto_backup_client.sqlite_store import SQLiteClientStore
from test_backup_pipeline import FakeBaiduForPipeline, FakeCloudForPipeline


TEST_PASSWORD = "Test123456789"
NOW = "2026-06-08T20:00:00Z"


class FakeCleanupOperator:
    def __init__(self) -> None:
        self.recycled: list[Path] = []
        self.quarantined: list[tuple[Path, Path]] = []
        self.deleted: list[Path] = []

    def move_to_recycle_bin(self, path: Path) -> None:
        self.recycled.append(path)

    def move_to_quarantine(self, path: Path, target: Path) -> None:
        self.quarantined.append((path, target))

    def permanent_delete(self, path: Path) -> None:
        self.deleted.append(path)


def test_cleanup_lists_only_completed_remote_confirmed_sources_as_eligible(tmp_path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("payload", encoding="utf-8")
    store, job_id = _completed_job(tmp_path, source)

    report = SourceCleanupService(store, device_id="device-1", operator=FakeCleanupOperator()).list_candidates(backup_job_id=job_id)

    assert report.eligible_count == 1
    assert report.blocked_count == 0
    candidate = report.candidates[0]
    assert candidate.eligible is True
    assert candidate.sync_pending_warning is False
    assert candidate.display_name == "source.txt"


def test_cleanup_blocks_when_source_file_identity_changed(tmp_path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("payload", encoding="utf-8")
    store, job_id = _completed_job(tmp_path, source)
    source.write_text("changed payload", encoding="utf-8")

    report = SourceCleanupService(store, device_id="device-1", operator=FakeCleanupOperator()).list_candidates(backup_job_id=job_id)

    assert report.eligible_count == 0
    assert report.candidates[0].candidate_status == "source_changed"


def test_cleanup_recycle_bin_writes_record_outbox_and_marks_reference_cleaned(tmp_path) -> None:
    source = tmp_path / "private" / "source.txt"
    source.parent.mkdir()
    source.write_text("payload", encoding="utf-8")
    store, job_id = _completed_job(tmp_path, source)
    operator = FakeCleanupOperator()

    result = SourceCleanupService(store, device_id="device-1", operator=operator).apply(
        backup_job_id=job_id,
        method="recycle_bin",
        confirm_text=CLEANUP_CONFIRM_TEXT,
        dry_run=False,
        now=NOW,
    )

    records = store.list_source_cleanup_records(job_id)
    references = store.list_content_references(job_id)
    with store.connect() as conn:
        outbox_payload = conn.execute(
            """
            SELECT payload_json
            FROM sync_outbox
            WHERE entity_type = 'source_cleanup_records'
            """
        ).fetchone()["payload_json"]

    assert result.applied_count == 1
    assert result.failed_count == 0
    assert operator.recycled == [source]
    assert records[0]["cleanup_status"] == "moved_to_recycle_bin"
    assert records[0]["cleanup_method"] == "recycle_bin"
    assert references[0]["cleanup_status"] == "cleaned"
    assert str(source) not in outbox_payload
    assert str(source.parent) not in outbox_payload


def test_cleanup_permanent_delete_requires_second_confirmation(tmp_path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("payload", encoding="utf-8")
    store, job_id = _completed_job(tmp_path, source)

    with pytest.raises(SourceCleanupError, match="permanent delete confirmation"):
        SourceCleanupService(store, device_id="device-1", operator=FakeCleanupOperator()).apply(
            backup_job_id=job_id,
            method="permanent_delete",
            confirm_text=CLEANUP_CONFIRM_TEXT,
            dry_run=False,
        )

    result = SourceCleanupService(store, device_id="device-1", operator=FakeCleanupOperator()).apply(
        backup_job_id=job_id,
        method="permanent_delete",
        confirm_text=CLEANUP_CONFIRM_TEXT,
        permanent_confirm_text=PERMANENT_DELETE_CONFIRM_TEXT,
        dry_run=True,
    )
    assert result.requested_count == 1


def test_cleanup_apply_rechecks_identity_before_file_operation(tmp_path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("payload", encoding="utf-8")
    store, job_id = _completed_job(tmp_path, source)
    operator = FakeCleanupOperator()

    service = SourceCleanupService(
        store,
        device_id="device-1",
        operator=operator,
        identity_reader=lambda _path: FileIdentity(size_bytes=999, mtime_ns=1, volume_serial="", file_index=""),
    )

    result = service.apply(
        backup_job_id=job_id,
        method="recycle_bin",
        confirm_text=CLEANUP_CONFIRM_TEXT,
        dry_run=False,
        now=NOW,
    )

    records = store.list_source_cleanup_records(job_id)
    assert result.applied_count == 0
    assert result.failed_count == 1
    assert operator.recycled == []
    assert records[0]["cleanup_status"] == "failed"
    assert records[0]["error_code"] == "source_changed"


def _completed_job(tmp_path, source: Path) -> tuple[SQLiteClientStore, str]:
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    created = BackupJobManager(store, device_id="device-1").create_job(
        [BackupSourceInput(str(source), "file")],
        now="2026-06-08T19:00:00Z",
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
            now="2026-06-08T19:05:00Z",
        ),
    )
    assert result.completed is True
    return store, job_id
