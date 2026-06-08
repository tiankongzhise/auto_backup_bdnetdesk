from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from auto_backup_client.archive_packager import ArchivePackageResult, ArchivePackager
from auto_backup_client.backup_jobs import BackupJobError, BackupJobManager, BackupJobWithSources
from auto_backup_client.baidu.reconcile import RemoteObjectReconciler, RemoteReconcileReport, RemoteReconcileScope, RequestRateLimiter
from auto_backup_client.baidu.resumable_upload import BaiduResumableUploader, ResumableArchiveInput, ResumableUploadResult
from auto_backup_client.baidu.upload import DEFAULT_BACKUP_ROOT_DIR, DEFAULT_PART_SIZE, BaiduNetdiskError, BaiduQuota
from auto_backup_client.dedupe_index import CloudCandidateResult, ContentDedupeIndexer
from auto_backup_client.scan_fingerprints import BackupScanner, JobScanResult
from auto_backup_client.sqlite_store import SQLiteClientStore, utc_now_iso
from auto_backup_client.sync_worker import SyncOutboxWorker, SyncWorkerResult


class PipelineBaiduClient(Protocol):
    def get_quota(self, *, checkfree: bool = True, checkexpire: bool = True) -> BaiduQuota:
        ...


class BackupPipelineError(ValueError):
    pass


@dataclass(frozen=True)
class BackupPipelineOptions:
    cache_root: Path | str
    password: str
    account_id: str = ""
    root_dir: str = DEFAULT_BACKUP_ROOT_DIR
    archive_seq: int = 1
    part_size: int = DEFAULT_PART_SIZE
    refresh_cloud_candidates: bool = False
    run_upload: bool = False
    check_quota: bool = True
    sync_outbox: bool = False
    reconcile_remote: bool = False
    mark_completed: bool = True
    sync_batch_size: int = 100
    max_sync_batches: int = 20
    now: str | None = None


@dataclass(frozen=True)
class BackupPipelineResult:
    backup_job_id: str
    final_stage: str
    completed: bool
    scan: JobScanResult
    content_index: object
    archive: ArchivePackageResult
    cloud_candidates: CloudCandidateResult | None = None
    upload: ResumableUploadResult | None = None
    sync: SyncWorkerResult | None = None
    reconcile: RemoteReconcileReport | None = None


class BackupPipeline:
    def __init__(
        self,
        *,
        store: SQLiteClientStore,
        device_id: str,
        baidu_client: object | None = None,
        cloud_client: object | None = None,
        seven_zip_path: str | Path | None = None,
        rate_limiter: RequestRateLimiter | None = None,
    ) -> None:
        cleaned_device_id = device_id.strip()
        if not cleaned_device_id:
            raise BackupPipelineError("device_id is required")
        self.store = store
        self.device_id = cleaned_device_id
        self.baidu_client = baidu_client
        self.cloud_client = cloud_client
        self.seven_zip_path = seven_zip_path
        self.rate_limiter = rate_limiter

    def run_job(self, backup_job_id: str, options: BackupPipelineOptions) -> BackupPipelineResult:
        cleaned_job_id = backup_job_id.strip()
        if not cleaned_job_id:
            raise BackupPipelineError("backup_job_id is required")
        _validate_options(options)
        self._validate_dependencies(options)

        manager = BackupJobManager(self.store, device_id=self.device_id)
        self._ensure_running(manager, cleaned_job_id, now=options.now)
        stage = "start"
        try:
            stage = "scan"
            scan = BackupScanner(self.store, device_id=self.device_id).scan_job(cleaned_job_id, now=options.now)

            stage = "dedupe"
            indexer = ContentDedupeIndexer(self.store, device_id=self.device_id)
            content_index = indexer.build_job_index(cleaned_job_id, now=options.now)

            cloud_candidates = None
            if options.refresh_cloud_candidates:
                stage = "cloud_candidates"
                cloud_candidates = indexer.refresh_cloud_candidates(
                    cleaned_job_id,
                    cloud_client=self.cloud_client,  # type: ignore[arg-type]
                    now=options.now,
                )

            stage = "archive"
            archive = ArchivePackager(
                self.store,
                device_id=self.device_id,
                seven_zip_path=self.seven_zip_path,
            ).package_job(
                cleaned_job_id,
                cache_root=options.cache_root,
                password=options.password,
                archive_seq=options.archive_seq,
                now=options.now,
            )

            upload = None
            if options.run_upload:
                stage = "upload"
                upload = self._upload_archive(cleaned_job_id, archive, options, manager.get_job_with_sources(cleaned_job_id))

            sync = None
            if options.sync_outbox:
                stage = "sync"
                sync = _run_sync_until_idle(
                    self.store,
                    self.cloud_client,  # type: ignore[arg-type]
                    batch_size=options.sync_batch_size,
                    max_batches=options.max_sync_batches,
                    now=options.now,
                )

            reconcile = None
            if options.reconcile_remote:
                stage = "reconcile"
                reconcile = RemoteObjectReconciler(
                    store=self.store,
                    baidu=self.baidu_client,  # type: ignore[arg-type]
                    rate_limiter=self.rate_limiter,
                ).reconcile(RemoteReconcileScope(job_id=cleaned_job_id, recursive=True))

            completed = _can_mark_completed(options, upload=upload, sync=sync, reconcile=reconcile)
            if completed:
                stage = "complete"
                manager.transition_job(cleaned_job_id, "completed", now=options.now)
                if options.sync_outbox:
                    stage = "final_sync"
                    final_sync = _run_sync_until_idle(
                        self.store,
                        self.cloud_client,  # type: ignore[arg-type]
                        batch_size=options.sync_batch_size,
                        max_batches=options.max_sync_batches,
                        now=options.now,
                    )
                    sync = _merge_sync_results(sync or SyncWorkerResult(selected=0, sent=0, synced=0, conflicts=0, rejected=0, retryable=0), final_sync)

            return BackupPipelineResult(
                backup_job_id=cleaned_job_id,
                final_stage="complete" if completed else stage,
                completed=completed,
                scan=scan,
                content_index=content_index,
                cloud_candidates=cloud_candidates,
                archive=archive,
                upload=upload,
                sync=sync,
                reconcile=reconcile,
            )
        except Exception as exc:
            self._mark_failed_retryable(manager, cleaned_job_id, now=options.now)
            if isinstance(exc, BackupPipelineError):
                raise
            raise BackupPipelineError(f"backup pipeline failed at stage: {stage}") from exc

    def _upload_archive(
        self,
        backup_job_id: str,
        archive: ArchivePackageResult,
        options: BackupPipelineOptions,
        job: BackupJobWithSources,
    ) -> ResumableUploadResult:
        baidu = self.baidu_client
        if baidu is None:
            raise BackupPipelineError("baidu_client is required when run_upload is enabled")
        if options.check_quota:
            quota = baidu.get_quota()  # type: ignore[attr-defined]
            if quota.available < archive.archive_size:
                raise BaiduNetdiskError("baidu netdisk available quota is smaller than archive size", error_code="quota_not_enough")
        result = BaiduResumableUploader(
            store=self.store,
            baidu=baidu,  # type: ignore[arg-type]
            updated_by_device_id=self.device_id,
        ).upload(
            ResumableArchiveInput(
                local_path=archive.archive_path,
                job_id=backup_job_id,
                device_id=self.device_id,
                account_id=options.account_id,
                archive_id=archive.archive_id,
                archive_seq=archive.archive_seq,
                archive_type=archive.archive_type,
                manifest_id=archive.manifest_id,
                root_dir=options.root_dir,
                job_created_at=_parse_utc_datetime(job.job.created_at),
                part_size=options.part_size,
            )
        )
        with self.store.transaction() as conn:
            self.store.update_archive_remote_path(
                conn,
                archive_id=archive.archive_id,
                remote_path=result.remote_archive_path,
                updated_by_device_id=self.device_id,
                now=options.now,
            )
        return result

    def _ensure_running(self, manager: BackupJobManager, backup_job_id: str, *, now: str | None) -> None:
        job = manager.get_job_with_sources(backup_job_id).job
        if job.status == "running":
            return
        if job.status in {"queued", "paused", "failed_retryable"}:
            manager.transition_job(backup_job_id, "running", now=now)
            return
        raise BackupPipelineError(f"backup job is not runnable from status: {job.status}")

    def _mark_failed_retryable(self, manager: BackupJobManager, backup_job_id: str, *, now: str | None) -> None:
        try:
            job = manager.get_job_with_sources(backup_job_id).job
            if job.status == "running":
                manager.transition_job(backup_job_id, "failed_retryable", now=now)
        except BackupJobError:
            return

    def _validate_dependencies(self, options: BackupPipelineOptions) -> None:
        if options.refresh_cloud_candidates and self.cloud_client is None:
            raise BackupPipelineError("cloud_client is required when refresh_cloud_candidates is enabled")
        if options.run_upload and self.baidu_client is None:
            raise BackupPipelineError("baidu_client is required when run_upload is enabled")
        if options.run_upload and not options.account_id.strip():
            raise BackupPipelineError("account_id is required when run_upload is enabled")
        if options.sync_outbox and self.cloud_client is None:
            raise BackupPipelineError("cloud_client is required when sync_outbox is enabled")
        if options.reconcile_remote and self.baidu_client is None:
            raise BackupPipelineError("baidu_client is required when reconcile_remote is enabled")


def _validate_options(options: BackupPipelineOptions) -> None:
    if not str(options.cache_root).strip():
        raise BackupPipelineError("cache_root is required")
    if not options.password:
        raise BackupPipelineError("archive password is required")
    if options.archive_seq < 1:
        raise BackupPipelineError("archive_seq must be >= 1")
    if options.part_size < DEFAULT_PART_SIZE:
        raise BackupPipelineError("part_size must be at least 4 MiB")
    if options.sync_batch_size < 1 or options.sync_batch_size > 100:
        raise BackupPipelineError("sync_batch_size must be between 1 and 100")
    if options.max_sync_batches < 1:
        raise BackupPipelineError("max_sync_batches must be >= 1")
    if options.mark_completed and options.run_upload and not options.sync_outbox:
        raise BackupPipelineError("sync_outbox is required before a job can be marked completed")
    if options.mark_completed and options.run_upload and not options.reconcile_remote:
        raise BackupPipelineError("reconcile_remote is required before a job can be marked completed")


def _run_sync_until_idle(
    store: SQLiteClientStore,
    cloud: object,
    *,
    batch_size: int,
    max_batches: int,
    now: str | None,
) -> SyncWorkerResult:
    totals = SyncWorkerResult(selected=0, sent=0, synced=0, conflicts=0, rejected=0, retryable=0)
    for _index in range(max_batches):
        result = SyncOutboxWorker(store=store, cloud=cloud, batch_size=batch_size).run_once(now=now or utc_now_iso())
        if result.selected == 0:
            break
        totals = _merge_sync_results(totals, result)
    return totals


def _merge_sync_results(left: SyncWorkerResult, right: SyncWorkerResult) -> SyncWorkerResult:
    return SyncWorkerResult(
        selected=left.selected + right.selected,
        sent=left.sent + right.sent,
        synced=left.synced + right.synced,
        conflicts=left.conflicts + right.conflicts,
        rejected=left.rejected + right.rejected,
        retryable=left.retryable + right.retryable,
        revision_results=left.revision_results + right.revision_results,
    )


def _can_mark_completed(
    options: BackupPipelineOptions,
    *,
    upload: ResumableUploadResult | None,
    sync: SyncWorkerResult | None,
    reconcile: RemoteReconcileReport | None,
) -> bool:
    if not options.mark_completed or upload is None or reconcile is None or reconcile.has_differences:
        return False
    if sync is not None and (sync.conflicts or sync.rejected or sync.retryable):
        return False
    return True


def _parse_utc_datetime(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
