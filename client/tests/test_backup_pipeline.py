from __future__ import annotations

from types import SimpleNamespace

import pytest

from auto_backup_client.backup_jobs import BackupJobManager, BackupSourceInput
from auto_backup_client.backup_pipeline import BackupPipeline, BackupPipelineError, BackupPipelineOptions
from auto_backup_client.scan_fingerprints import FileFingerprint, fingerprint_file
from auto_backup_client.baidu.models import SyncRevisionResult
from auto_backup_client.baidu.upload import (
    BaiduFileItem,
    BaiduFileListResult,
    BaiduNetdiskError,
    BaiduQuota,
    CreateFileResult,
    FileManagerResult,
    LocateUploadResult,
    PrecreateResult,
    UploadPartResult,
)
from auto_backup_client.sqlite_store import SQLiteClientStore


TEST_ARCHIVE_PASSWORD = "Test123456789"


class FakeBaiduForPipeline:
    def __init__(self, *, fail_precreate: bool = False, available_quota: int = 1024 * 1024 * 1024) -> None:
        self.fail_precreate = fail_precreate
        self.available_quota = available_quota
        self.uploaded_partseqs: list[int] = []
        self.created_files: dict[str, CreateFileResult] = {}
        self.created_sizes: dict[str, int] = {}

    def get_quota(self, *, checkfree: bool = True, checkexpire: bool = True) -> BaiduQuota:
        del checkfree, checkexpire
        return BaiduQuota(total=self.available_quota, used=0)

    def precreate(self, **kwargs):
        if self.fail_precreate:
            raise BaiduNetdiskError("network interrupted", error_code="http_request_failed")
        return PrecreateResult(path=kwargs["remote_path"], uploadid="pipeline-uploadid", return_type=1, block_list=tuple(range(len(kwargs["block_md5s"]))))

    def locate_upload_server(self, **_kwargs):
        return LocateUploadResult(upload_server="https://upload.example.test", servers=("https://upload.example.test",))

    def upload_part(self, **kwargs):
        partseq = kwargs["partseq"]
        self.uploaded_partseqs.append(partseq)
        part = kwargs["plan"].part_by_seq(partseq)
        return UploadPartResult(partseq=partseq, md5=part.md5)

    def create_file(self, **kwargs):
        remote_path = kwargs["remote_path"]
        result = CreateFileResult(
            fs_id=1000 + len(self.created_files),
            path=remote_path,
            md5="c" * 32,
            server_filename=remote_path.rsplit("/", 1)[-1],
        )
        self.created_files[remote_path] = result
        self.created_sizes[remote_path] = int(kwargs["size"])
        return result

    def upload_file_complete(self, *, local_path, remote_path: str, part_size: int, rtype: int):
        del part_size, rtype
        result = CreateFileResult(
            fs_id=2000 + len(self.created_files),
            path=remote_path,
            md5="d" * 32,
            server_filename=remote_path.rsplit("/", 1)[-1],
        )
        self.created_files[remote_path] = result
        self.created_sizes[remote_path] = int(local_path.stat().st_size)
        return SimpleNamespace(created=result)

    def delete_files(self, remote_paths, *, async_mode: int = 0):
        assert async_mode == 0
        for path in remote_paths:
            self.created_files.pop(path, None)
            self.created_sizes.pop(path, None)
        return FileManagerResult(errno=0, info=tuple())

    def list_all(self, *, remote_path: str, start: int = 0, limit: int = 1000, recursion: bool = True, web: bool = False):
        del remote_path, start, limit, recursion, web
        return BaiduFileListResult(
            errno=0,
            items=tuple(
                BaiduFileItem(
                    fs_id=result.fs_id,
                    path=path,
                    server_filename=result.server_filename,
                    isdir=False,
                    size=self.created_sizes[path],
                    md5=result.md5,
                )
                for path, result in sorted(self.created_files.items())
            ),
        )


class FakeBaiduWithExtraRemote(FakeBaiduForPipeline):
    def list_all(self, *, remote_path: str, start: int = 0, limit: int = 1000, recursion: bool = True, web: bool = False):
        result = super().list_all(remote_path=remote_path, start=start, limit=limit, recursion=recursion, web=web)
        return BaiduFileListResult(
            errno=0,
            items=result.items
            + (
                BaiduFileItem(
                    fs_id=9999,
                    path=remote_path.rstrip("/") + "/unexpected.7z",
                    server_filename="unexpected.7z",
                    isdir=False,
                    size=1,
                    md5="e" * 32,
                ),
            ),
        )


class FakeCloudForPipeline:
    def __init__(self) -> None:
        self.synced_event_ids: list[str] = []

    def sync_revisions(self, events):
        results = []
        for event in events:
            self.synced_event_ids.append(event.event_id)
            results.append(
                SyncRevisionResult(
                    event_id=event.event_id,
                    entity_id=event.entity_id,
                    revision_id=event.revision_id,
                    status="synced",
                    cloud_data_version=event.data_version,
                    cloud_revision_id=event.revision_id,
                )
            )
        return results


def test_pipeline_runs_local_scan_dedupe_and_archive_without_marking_completed(tmp_path) -> None:
    source = tmp_path / "source.txt"
    duplicate = tmp_path / "duplicate.txt"
    source.write_text("same", encoding="utf-8")
    duplicate.write_text("same", encoding="utf-8")
    store, job_id = _job(store_path=tmp_path / "backup_state.sqlite3", sources=[source, duplicate])

    result = BackupPipeline(store=store, device_id="device-1").run_job(
        job_id,
        BackupPipelineOptions(
            cache_root=tmp_path / "cache",
            password=TEST_ARCHIVE_PASSWORD,
            mark_completed=False,
            now="2026-06-08T09:00:00Z",
        ),
    )

    assert result.final_stage == "archive"
    assert result.completed is False
    assert result.scan.file_count == 2
    assert len(result.archives) == 2
    assert [archive.archive_type for archive in result.archives] == ["payload", "manifest_only"]
    assert sum(archive.payload_member_count for archive in result.archives) == 1
    assert sum(archive.reference_member_count for archive in result.archives) == 1
    assert result.upload is None
    assert result.uploads == ()
    assert result.sync is None
    assert result.reconcile is None

    job = store.get_backup_job(job_id)
    assert job is not None
    assert job["status"] == "running"
    assert store.list_archives(job_id)[0]["remote_path"] == ""


def test_pipeline_packages_each_selected_source_as_separate_archive(tmp_path) -> None:
    first = tmp_path / "first.txt"
    folder = tmp_path / "photos"
    folder.mkdir()
    nested = folder / "nested" / "image.jpg"
    nested.parent.mkdir()
    first.write_text("one", encoding="utf-8")
    nested.write_text("two", encoding="utf-8")
    store, job_id = _job(store_path=tmp_path / "backup_state.sqlite3", sources=[first, folder])

    result = BackupPipeline(store=store, device_id="device-1").run_job(
        job_id,
        BackupPipelineOptions(
            cache_root=tmp_path / "cache",
            password=TEST_ARCHIVE_PASSWORD,
            mark_completed=False,
            now="2026-06-08T09:05:00Z",
        ),
    )

    archives = store.list_archives(job_id)
    references = store.list_content_references(job_id)

    assert len(result.archives) == 2
    assert [archive.archive_seq for archive in result.archives] == [1, 2]
    assert len(archives) == 2
    assert {row["archive_id"] for row in references} == {archive.archive_id for archive in result.archives}


def test_pipeline_skips_file_that_changes_between_scan_and_packaging(tmp_path) -> None:
    stable = tmp_path / "stable.txt"
    changing = tmp_path / "LibreHardwareMonitorLog-2026-06-14-141.csv"
    stable.write_text("stable", encoding="utf-8")
    changing.write_text("before", encoding="utf-8")
    store, job_id = _job(store_path=tmp_path / "backup_state.sqlite3", sources=[stable, changing])

    original_fingerprint = fingerprint_file
    changed_once = {"done": False}

    def changing_fingerprint(path):
        result = original_fingerprint(path)
        if path.name == changing.name and not changed_once["done"]:
            path.write_text("after", encoding="utf-8")
            changed_once["done"] = True
        return result

    pipeline = BackupPipeline(store=store, device_id="device-1")

    from auto_backup_client import scan_fingerprints

    original_module_fingerprint = scan_fingerprints.fingerprint_file
    scan_fingerprints.fingerprint_file = changing_fingerprint
    try:
        result = pipeline.run_job(
            job_id,
            BackupPipelineOptions(
                cache_root=tmp_path / "cache",
                password=TEST_ARCHIVE_PASSWORD,
                mark_completed=False,
                now="2026-06-08T09:06:00Z",
            ),
        )
    finally:
        scan_fingerprints.fingerprint_file = original_module_fingerprint

    files = store.list_file_items(job_id)
    references = store.list_content_references(job_id)

    assert result.completed is False
    assert len(result.archives) == 1
    assert [row["display_name"] for row in references] == ["stable.txt"]
    assert {row["display_name"]: row["scan_status"] for row in files}[changing.name] == "changed_during_scan"


def test_pipeline_uploads_syncs_reconciles_and_marks_job_completed(tmp_path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("payload", encoding="utf-8")
    store, job_id = _job(store_path=tmp_path / "backup_state.sqlite3", sources=[source])
    baidu = FakeBaiduForPipeline()
    cloud = FakeCloudForPipeline()

    result = BackupPipeline(store=store, device_id="device-1", baidu_client=baidu, cloud_client=cloud).run_job(
        job_id,
        BackupPipelineOptions(
            cache_root=tmp_path / "cache",
            password=TEST_ARCHIVE_PASSWORD,
            account_id="account-1",
            run_upload=True,
            sync_outbox=True,
            reconcile_remote=True,
            now="2026-06-08T09:10:00Z",
        ),
    )

    assert result.completed is True
    assert result.final_stage == "complete"
    assert result.upload is not None
    assert result.upload.archive_id == result.archive.archive_id
    assert result.reconcile is not None
    assert result.reconcile.status_counts["consistent"] == 3
    assert result.sync is not None
    assert result.sync.retryable == 0
    assert result.sync.selected >= 1
    assert cloud.synced_event_ids

    archive = store.list_archives(job_id)[0]
    remote_objects = store.list_remote_objects_for_reconcile(job_id=job_id)
    job = store.get_backup_job(job_id)
    assert archive["remote_path"] == result.upload.remote_archive_path
    assert {row["object_type"] for row in remote_objects} == {"archive", "archive_meta", "job_index"}
    assert all(row["archive_id"] == result.archive.archive_id or row["object_type"] == "job_index" for row in remote_objects)
    assert job is not None
    assert job["status"] == "completed"
    assert job["sync_status"] == "synced"


def test_pipeline_reconcile_difference_marks_job_failed_retryable_with_stage_reason(tmp_path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("payload", encoding="utf-8")
    store, job_id = _job(store_path=tmp_path / "backup_state.sqlite3", sources=[source])

    result = BackupPipeline(
        store=store,
        device_id="device-1",
        baidu_client=FakeBaiduWithExtraRemote(),
        cloud_client=FakeCloudForPipeline(),
    ).run_job(
        job_id,
        BackupPipelineOptions(
            cache_root=tmp_path / "cache",
            password=TEST_ARCHIVE_PASSWORD,
            account_id="account-1",
            run_upload=True,
            sync_outbox=True,
            reconcile_remote=True,
            now="2026-06-08T09:15:00Z",
        ),
    )

    job = store.get_backup_job(job_id)
    assert result.completed is False
    assert result.final_stage == "reconcile"
    assert result.sync is not None
    assert result.sync.retryable == 0
    assert result.reconcile is not None
    assert result.reconcile.status_counts["baidu_only"] == 1
    assert job is not None
    assert job["status"] == "failed_retryable"
    assert job["last_stage"] == "reconcile"
    assert "baidu_only" in job["last_error"]


def test_pipeline_upload_failure_keeps_job_retryable_and_never_completed(tmp_path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("payload", encoding="utf-8")
    store, job_id = _job(store_path=tmp_path / "backup_state.sqlite3", sources=[source])

    with pytest.raises(BackupPipelineError, match="stage: upload"):
        BackupPipeline(store=store, device_id="device-1", baidu_client=FakeBaiduForPipeline(fail_precreate=True)).run_job(
            job_id,
            BackupPipelineOptions(
                cache_root=tmp_path / "cache",
                password=TEST_ARCHIVE_PASSWORD,
                account_id="account-1",
                run_upload=True,
                mark_completed=False,
                now="2026-06-08T09:20:00Z",
            ),
        )

    job = store.get_backup_job(job_id)
    assert job is not None
    assert job["status"] == "failed_retryable"
    assert store.list_archives(job_id)
    assert store.list_remote_objects_for_reconcile(job_id=job_id) == []


def test_pipeline_requires_reconcile_before_completion_after_upload(tmp_path) -> None:
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()

    with pytest.raises(BackupPipelineError, match="reconcile_remote is required"):
        BackupPipeline(store=store, device_id="device-1", baidu_client=FakeBaiduForPipeline()).run_job(
            "job-1",
            BackupPipelineOptions(
                cache_root=tmp_path / "cache",
                password=TEST_ARCHIVE_PASSWORD,
                account_id="account-1",
                run_upload=True,
                sync_outbox=True,
                mark_completed=True,
            ),
        )


def test_pipeline_requires_sync_before_completion_after_upload(tmp_path) -> None:
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()

    with pytest.raises(BackupPipelineError, match="sync_outbox is required"):
        BackupPipeline(store=store, device_id="device-1", baidu_client=FakeBaiduForPipeline()).run_job(
            "job-1",
            BackupPipelineOptions(
                cache_root=tmp_path / "cache",
                password=TEST_ARCHIVE_PASSWORD,
                account_id="account-1",
                run_upload=True,
                reconcile_remote=True,
                mark_completed=True,
            ),
        )


def test_pipeline_cache_budget_failure_keeps_job_queued(tmp_path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("payload", encoding="utf-8")
    store, job_id = _job(store_path=tmp_path / "backup_state.sqlite3", sources=[source])

    with pytest.raises(BackupPipelineError, match="effective budget"):
        BackupPipeline(store=store, device_id="device-1").run_job(
            job_id,
            BackupPipelineOptions(
                cache_root=tmp_path / "cache",
                password=TEST_ARCHIVE_PASSWORD,
                enforce_cache_budget=True,
                cache_quota_bytes=1,
                min_effective_cache_budget_bytes=2,
                max_archive_size_bytes=1,
                mark_completed=False,
            ),
        )

    job = store.get_backup_job(job_id)
    assert job is not None
    assert job["status"] == "queued"


def test_pipeline_records_artifacts_and_cleanup_dry_run_after_completed(tmp_path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("payload", encoding="utf-8")
    store, job_id = _job(store_path=tmp_path / "backup_state.sqlite3", sources=[source])
    baidu = FakeBaiduForPipeline()
    cloud = FakeCloudForPipeline()

    result = BackupPipeline(store=store, device_id="device-1", baidu_client=baidu, cloud_client=cloud).run_job(
        job_id,
        BackupPipelineOptions(
            cache_root=tmp_path / "cache",
            password=TEST_ARCHIVE_PASSWORD,
            account_id="account-1",
            run_upload=True,
            sync_outbox=True,
            reconcile_remote=True,
            cleanup_cache_artifacts=True,
            cleanup_cache_dry_run=True,
            cache_quota_bytes=1024 * 1024 * 1024,
            min_effective_cache_budget_bytes=0,
            now="2026-06-08T09:30:00Z",
        ),
    )

    artifacts = store.list_cache_artifacts(job_id=job_id, include_deleted=True)
    assert result.completed is True
    assert result.cache_cleanup is not None
    assert result.cache_cleanup.dry_run is True
    assert {row["artifact_type"] for row in artifacts} >= {"archive", "manifest_plain", "staging", "verify"}
    assert any(row["artifact_type"] == "archive" and row["remote_confirmed"] == 1 for row in artifacts)
    assert all("\\\\?\\" not in row["artifact_path"] for row in artifacts)
    assert all("\\\\?\\" not in path_hash for path_hash in result.cache_cleanup.path_sha256s)


def _job(*, store_path, sources) -> tuple[SQLiteClientStore, str]:
    store = SQLiteClientStore(store_path)
    store.migrate()
    created = BackupJobManager(store, device_id="device-1").create_job(
        [BackupSourceInput(str(source), "directory" if source.is_dir() else "file") for source in sources],
        now="2026-06-08T09:00:00Z",
    )
    return store, created.job.backup_job_id
