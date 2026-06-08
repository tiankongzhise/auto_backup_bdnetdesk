from __future__ import annotations

from types import SimpleNamespace

import pytest

from auto_backup_client.backup_jobs import BackupJobManager, BackupSourceInput
from auto_backup_client.backup_pipeline import BackupPipeline, BackupPipelineError, BackupPipelineOptions
from auto_backup_client.baidu.models import SyncRevisionResult
from auto_backup_client.baidu.upload import (
    BaiduFileItem,
    BaiduFileListResult,
    BaiduNetdiskError,
    BaiduQuota,
    CreateFileResult,
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
    assert result.archive.archive_type == "mixed"
    assert result.archive.payload_member_count == 1
    assert result.upload is None
    assert result.sync is None
    assert result.reconcile is None

    job = store.get_backup_job(job_id)
    assert job is not None
    assert job["status"] == "running"
    assert store.list_archives(job_id)[0]["remote_path"] == ""


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


def _job(*, store_path, sources) -> tuple[SQLiteClientStore, str]:
    store = SQLiteClientStore(store_path)
    store.migrate()
    created = BackupJobManager(store, device_id="device-1").create_job(
        [BackupSourceInput(str(source), "file") for source in sources],
        now="2026-06-08T09:00:00Z",
    )
    return store, created.job.backup_job_id
