from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import sqlite3
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from auto_backup_client.backup_jobs import BackupJobManager, BackupSourceInput
from auto_backup_client.backup_pipeline import BackupPipeline, BackupPipelineError, BackupPipelineOptions
from auto_backup_client.baidu.auth_workflow import BaiduAuthWorkflow
from auto_backup_client.baidu.cloud_api import BaiduCloudClient, CloudAPIError
from auto_backup_client.baidu.upload import DEFAULT_BACKUP_ROOT_DIR, BaiduNetdiskClient, BaiduNetdiskError
from auto_backup_client.device_credentials import resolve_or_register_device_credentials
from auto_backup_client.sqlite_store import OutboxEvent, SQLiteClientStore, utc_now_iso
from auto_backup_client.sync_worker import SUCCESS_STATUSES, SyncOutboxWorker, SyncWorkerResult


DEFAULT_SMALL_BYTES = 64 * 1024
DEFAULT_MULTIPART_BYTES = 4 * 1024 * 1024 + 65_537


@dataclass(frozen=True)
class CleanupResult:
    object_count: int
    delete_errno: int = -1
    kept_remote: bool = False
    path_hashes: tuple[str, ...] = tuple()


@dataclass(frozen=True)
class ConflictProbeResult:
    attempted: bool
    detected: bool
    error_code: str = ""


def main(argv: Sequence[str] | None = None) -> int:
    _load_local_env_files()
    parser = argparse.ArgumentParser(description="真实 BackupPipeline 百度上传全链路测试入口。")
    parser.add_argument("--base-url", default=os.environ.get("CLOUD_API_BASE_URL", "https://backup.baichengedu.com"))
    parser.add_argument(
        "--device-token-env",
        default="CLOUD_API_DEVICE_TOKEN",
        help="读取 Device Token 的环境变量名；未设置时复用本机 DPAPI 凭据。",
    )
    parser.add_argument("--password-env", default="", help="从指定环境变量读取授权/归档密码。")
    parser.add_argument("--sqlite-path", default="", help="本地 SQLite 路径；默认使用仓库内 .cache 临时库。")
    parser.add_argument("--cache-root", default="", help="缓存目录；默认使用仓库内 .cache 临时目录。")
    parser.add_argument("--work-dir", default="", help="临时源文件目录；默认使用仓库内 .cache 临时目录。")
    parser.add_argument("--job-name", default="真实百度上传全链路测试")
    parser.add_argument("--job-id", default="", help="可选：复用已有 job_id 继续跑测。")
    parser.add_argument("--account-id", default="", help="百度账号 ID；省略时使用当前设备已选择账号。")
    parser.add_argument("--root-dir", default=DEFAULT_BACKUP_ROOT_DIR)
    parser.add_argument("--part-size-mib", type=int, default=4, choices=(4, 16, 32))
    parser.add_argument("--small-bytes", type=int, default=DEFAULT_SMALL_BYTES)
    parser.add_argument("--multipart-bytes", type=int, default=DEFAULT_MULTIPART_BYTES)
    parser.add_argument("--sync-batch-size", type=int, default=100)
    parser.add_argument("--max-sync-batches", type=int, default=20)
    parser.add_argument("--verify-limit", type=int, default=100)
    parser.add_argument("--skip-cloud-candidates", action="store_true", help="跳过去重后的云端候选查询。")
    parser.add_argument("--skip-conflict-probe", action="store_true", help="跳过同路径 rtype=0 冲突探针。")
    parser.add_argument("--skip-quota-check", action="store_true", help="跳过百度容量检查。")
    parser.add_argument("--keep-remote", action="store_true", help="保留本批远端对象；默认跑完用 filemanager/delete 清理。")

    args = parser.parse_args(argv)
    try:
        return _run(args)
    except (BackupPipelineError, CloudAPIError, BaiduNetdiskError, ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
        _print(f"操作失败: {_safe_error_summary(exc)}")
        return 1


def _run(args: argparse.Namespace) -> int:
    if args.small_bytes < 1:
        raise ValueError("small_bytes must be >= 1")
    if args.multipart_bytes <= args.part_size_mib * 1024 * 1024:
        raise ValueError("multipart_bytes must exceed one upload part")
    if args.verify_limit < 0:
        raise ValueError("verify_limit must be >= 0")

    password = _read_authorization_password(args.password_env)
    credentials, source = _resolve_credentials(args)
    run_id = f"real-pipeline-{uuid.uuid4().hex[:12]}"
    sqlite_path = _path_arg(args.sqlite_path, ".cache", "real-pipeline", run_id, "backup_state.sqlite3")
    cache_root = _path_arg(args.cache_root, ".cache", "real-pipeline", run_id, "cache")
    work_dir = _path_arg(args.work_dir, ".cache", "real-pipeline", run_id, "sources")

    store = SQLiteClientStore(sqlite_path)
    store.migrate()
    source_paths = _prepare_sources(work_dir, small_bytes=args.small_bytes, multipart_bytes=args.multipart_bytes, run_id=run_id)
    device_id = credentials.device_id or "environment"
    job_id = args.job_id.strip() or _create_job(store, device_id=device_id, job_name=args.job_name, sources=source_paths)

    with BaiduCloudClient(args.base_url, credentials.device_token, timeout=30.0) as cloud:
        decrypted = _decrypt_selected_token(cloud, args.account_id, password)
        account_id = decrypted.encrypted.account_id
        with BaiduNetdiskClient(decrypted.token.access_token, timeout=120.0) as baidu:
            pipeline = BackupPipeline(store=store, device_id=device_id, baidu_client=baidu, cloud_client=cloud)
            result = pipeline.run_job(
                job_id,
                BackupPipelineOptions(
                    cache_root=cache_root,
                    password=password,
                    account_id=account_id,
                    root_dir=args.root_dir,
                    part_size=args.part_size_mib * 1024 * 1024,
                    refresh_cloud_candidates=not args.skip_cloud_candidates,
                    run_upload=True,
                    check_quota=not args.skip_quota_check,
                    sync_outbox=True,
                    reconcile_remote=True,
                    mark_completed=True,
                    sync_batch_size=args.sync_batch_size,
                    max_sync_batches=args.max_sync_batches,
                    enforce_cache_budget=True,
                    cleanup_cache_artifacts=True,
                    cleanup_cache_dry_run=True,
                ),
            )
            remaining_before_final = store.list_outbox_events_for_sync(limit=args.sync_batch_size, now=utc_now_iso())
            final_sync = _run_sync_until_idle(store, cloud, batch_size=args.sync_batch_size, max_batches=args.max_sync_batches)
            verified = _verify_cloud_summaries(cloud, remaining_before_final, final_sync, limit=args.verify_limit)
            completed_summary_verified = _verify_completed_job_summary(cloud, store, job_id)
            conflict = _probe_same_path_conflict(baidu, result.upload.remote_archive_path, result.archive.archive_path, args.part_size_mib * 1024 * 1024) if result.upload and not args.skip_conflict_probe else ConflictProbeResult(attempted=False, detected=False)
            cleanup = _cleanup_remote_objects(store, baidu, job_id=job_id, keep_remote=args.keep_remote)
            _validate_real_result(
                result=result,
                final_sync=final_sync,
                completed_summary_verified=completed_summary_verified,
                conflict=conflict,
                cleanup=cleanup,
                keep_remote=args.keep_remote,
                skip_conflict_probe=args.skip_conflict_probe,
            )

    _print(f"Device Token 来源: {source}")
    _print(f"account_id_sha256: {_sha256_text(account_id)}")
    _print(f"token_version: {decrypted.encrypted.token_version}")
    _print(f"job_id: {job_id}")
    _print(f"completed: {str(result.completed).lower()}")
    _print(f"final_stage: {result.final_stage}")
    _print(f"scan_files: {result.scan.file_count}")
    _print(f"scan_folders: {result.scan.folder_count}")
    _print(f"scan_issues: {result.scan.issue_count}")
    _print(f"content_references: {result.content_index.reference_count}")
    _print(f"payload_sources: {result.content_index.payload_source_count}")
    if result.cloud_candidates is not None:
        _print(f"cloud_candidates_checked: {result.cloud_candidates.checked_content_count}")
        _print(f"cloud_duplicate_candidates: {result.cloud_candidates.cloud_duplicate_candidate_count}")
        _print(f"cloud_missing: {result.cloud_candidates.missing_count}")
    _print(f"archive_id: {result.archive.archive_id}")
    _print(f"archive_sha256: {result.archive.archive_sha256}")
    _print(f"archive_size: {result.archive.archive_size}")
    _print(f"archive_type: {result.archive.archive_type}")
    if result.cache_usage is not None:
        _print(f"cache_level_before: {result.cache_usage.level}")
        _print(f"cache_effective_budget_bytes_before: {result.cache_usage.effective_budget_bytes}")
    if result.cache_cleanup is not None:
        _print(f"cache_cleanup_selected_count: {result.cache_cleanup.selected_count}")
        _print(f"cache_cleanup_dry_run: {str(result.cache_cleanup.dry_run).lower()}")
    _print(f"manifest_sha256: {result.archive.manifest_sha256}")
    if result.upload is not None:
        _print(f"upload_session_id: {result.upload.upload_session_id}")
        _print(f"uploaded_part_count: {len(result.upload.uploaded_partseqs)}")
        _print(f"archive_fs_id: {result.upload.created.fs_id}")
        _print(f"meta_fs_id: {result.upload.meta_created.fs_id}")
        _print(f"job_index_fs_id: {result.upload.job_index_created.fs_id}")
        _print(f"remote_archive_path_sha256: {_sha256_text(result.upload.remote_archive_path)}")
        _print(f"remote_meta_path_sha256: {_sha256_text(result.upload.remote_meta_path)}")
        _print(f"remote_job_index_path_sha256: {_sha256_text(result.upload.remote_job_index_path)}")
    if result.sync is not None:
        _print(f"sync_selected: {result.sync.selected}")
        _print(f"sync_sent: {result.sync.sent}")
        _print(f"sync_synced: {result.sync.synced}")
        _print(f"sync_conflicts: {result.sync.conflicts}")
        _print(f"sync_rejected: {result.sync.rejected}")
        _print(f"sync_retryable: {result.sync.retryable}")
    if result.reconcile is not None:
        for status, count in sorted(result.reconcile.status_counts.items()):
            _print(f"reconcile_{status}: {count}")
    _print(f"final_sync_selected: {final_sync.selected}")
    _print(f"final_sync_synced: {final_sync.synced}")
    _print(f"final_sync_conflicts: {final_sync.conflicts}")
    _print(f"final_sync_rejected: {final_sync.rejected}")
    _print(f"final_sync_retryable: {final_sync.retryable}")
    _print(f"cloud_summary_verified: {verified}")
    _print(f"completed_job_cloud_summary_verified: {str(completed_summary_verified).lower()}")
    _print(f"conflict_probe_attempted: {str(conflict.attempted).lower()}")
    _print(f"conflict_probe_detected: {str(conflict.detected).lower()}")
    if conflict.error_code:
        _print(f"conflict_probe_error_code: {conflict.error_code}")
    _print(f"cleanup_object_count: {cleanup.object_count}")
    if cleanup.kept_remote:
        _print("cleanup_kept_remote: true")
    else:
        _print(f"cleanup_delete_errno: {cleanup.delete_errno}")
    for index, path_hash in enumerate(cleanup.path_hashes, start=1):
        _print(f"cleanup_path_{index}_sha256: {path_hash}")
    _print("真实全链路测试完成: scan -> dedupe -> 7zip -> upload -> sync -> reconcile -> completed -> cleanup")
    return 0


def _load_local_env_files() -> None:
    candidates = (
        Path.cwd() / ".env",
        Path(__file__).resolve().parents[2] / ".env",
    )
    for path in candidates:
        if not path.is_file():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            cleaned_key = key.strip()
            if not cleaned_key or cleaned_key in os.environ:
                continue
            os.environ[cleaned_key] = _unquote_env_value(value.strip())


def _unquote_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _path_arg(value: str, *default_parts: str) -> Path:
    cleaned = value.strip()
    return Path(cleaned) if cleaned else Path(*default_parts)


def _prepare_sources(work_dir: Path, *, small_bytes: int, multipart_bytes: int, run_id: str) -> tuple[Path, Path]:
    work_dir.mkdir(parents=True, exist_ok=True)
    small = work_dir / "small-source.bin"
    multipart = work_dir / "multipart-source.bin"
    seed_prefix = run_id.encode("utf-8")
    _write_deterministic_file(small, small_bytes, seed=seed_prefix + b":small")
    _write_deterministic_file(multipart, multipart_bytes, seed=seed_prefix + b":multipart")
    return small, multipart


def _write_deterministic_file(path: Path, size: int, *, seed: bytes) -> None:
    remaining = size
    counter = 0
    with path.open("wb") as handle:
        while remaining > 0:
            block = hashlib.sha256(seed + counter.to_bytes(8, "big")).digest()
            handle.write(block[:remaining] if remaining < len(block) else block)
            remaining -= min(remaining, len(block))
            counter += 1


def _create_job(store: SQLiteClientStore, *, device_id: str, job_name: str, sources: Sequence[Path]) -> str:
    created = BackupJobManager(store, device_id=device_id).create_job(
        [BackupSourceInput(str(source), "file") for source in sources],
        job_name=job_name,
    )
    return created.job.backup_job_id


def _run_sync_until_idle(store: SQLiteClientStore, cloud: BaiduCloudClient, *, batch_size: int, max_batches: int) -> SyncWorkerResult:
    totals = SyncWorkerResult(selected=0, sent=0, synced=0, conflicts=0, rejected=0, retryable=0)
    for _index in range(max_batches):
        result = SyncOutboxWorker(store=store, cloud=cloud, batch_size=batch_size).run_once()
        if result.selected == 0:
            break
        totals = SyncWorkerResult(
            selected=totals.selected + result.selected,
            sent=totals.sent + result.sent,
            synced=totals.synced + result.synced,
            conflicts=totals.conflicts + result.conflicts,
            rejected=totals.rejected + result.rejected,
            retryable=totals.retryable + result.retryable,
            revision_results=totals.revision_results + result.revision_results,
        )
    return totals


def _verify_cloud_summaries(cloud: BaiduCloudClient, events: Sequence[OutboxEvent], result: SyncWorkerResult, *, limit: int) -> int:
    successful_ids = {item.event_id for item in result.revision_results if item.status in SUCCESS_STATUSES}
    verified = 0
    for event in [item for item in events if item.event_id in successful_ids][:limit]:
        summary = cloud.get_entity_summary(event.entity_id)
        if (
            summary.revision_id == event.revision_id
            and summary.data_version == event.data_version
            and summary.canonical_record_sha256 == event.canonical_record_sha256
        ) or any(
            revision.revision_id == event.revision_id
            and revision.data_version == event.data_version
            and revision.canonical_record_sha256 == event.canonical_record_sha256
            for revision in summary.recent_revisions
        ):
            verified += 1
    return verified


def _verify_completed_job_summary(cloud: BaiduCloudClient, store: SQLiteClientStore, job_id: str) -> bool:
    row = store.get_backup_job(job_id)
    if row is None or str(row.get("status", "")) != "completed":
        return False
    summary = cloud.get_entity_summary(str(row["entity_id"]))
    revision_id = str(row["revision_id"])
    data_version = int(row["data_version"])
    record_hash = str(row["canonical_record_sha256"])
    if (
        summary.revision_id == revision_id
        and summary.data_version == data_version
        and summary.canonical_record_sha256 == record_hash
    ):
        return True
    return any(
        revision.revision_id == revision_id
        and revision.data_version == data_version
        and revision.canonical_record_sha256 == record_hash
        for revision in summary.recent_revisions
    )


def _validate_real_result(
    *,
    result: object,
    final_sync: SyncWorkerResult,
    completed_summary_verified: bool,
    conflict: ConflictProbeResult,
    cleanup: CleanupResult,
    keep_remote: bool,
    skip_conflict_probe: bool,
) -> None:
    if not getattr(result, "completed", False):
        raise ValueError("real pipeline did not complete")
    upload = getattr(result, "upload", None)
    if upload is None:
        raise ValueError("real pipeline upload is missing")
    if len(upload.uploaded_partseqs) < 2:
        raise ValueError("real pipeline multipart upload did not cross part boundary")
    sync = getattr(result, "sync", None)
    if sync is None or sync.conflicts or sync.rejected or sync.retryable:
        raise ValueError("real pipeline sync reported failures")
    reconcile = getattr(result, "reconcile", None)
    if reconcile is None or reconcile.has_differences:
        raise ValueError("real pipeline reconcile reported differences")
    if reconcile.status_counts.get("consistent", 0) != 3:
        raise ValueError("real pipeline reconcile did not confirm all remote objects")
    if final_sync.conflicts or final_sync.rejected or final_sync.retryable:
        raise ValueError("real pipeline final sync reported failures")
    if not completed_summary_verified:
        raise ValueError("completed job cloud summary mismatch")
    if not skip_conflict_probe and not conflict.detected:
        raise ValueError("same-path conflict probe did not detect conflict")
    if cleanup.object_count != 3:
        raise ValueError("remote cleanup did not find expected objects")
    if not keep_remote and cleanup.delete_errno != 0:
        raise ValueError("remote cleanup did not delete expected objects")


def _probe_same_path_conflict(baidu: BaiduNetdiskClient, remote_path: str, archive_path: Path, part_size: int) -> ConflictProbeResult:
    try:
        baidu.upload_file_complete(local_path=archive_path, remote_path=remote_path, part_size=part_size, rtype=0)
    except BaiduNetdiskError as exc:
        return ConflictProbeResult(attempted=True, detected=True, error_code=exc.error_code or "unknown")
    return ConflictProbeResult(attempted=True, detected=False)


def _cleanup_remote_objects(store: SQLiteClientStore, baidu: BaiduNetdiskClient, *, job_id: str, keep_remote: bool) -> CleanupResult:
    rows = store.list_remote_objects_for_cleanup(job_id=job_id)
    remote_paths = tuple(str(row["remote_path"]) for row in rows if str(row.get("remote_path", "")).strip())
    path_hashes = tuple(_sha256_text(path) for path in remote_paths)
    if not remote_paths:
        return CleanupResult(object_count=0, delete_errno=-1, kept_remote=keep_remote, path_hashes=path_hashes)
    if keep_remote:
        return CleanupResult(object_count=len(remote_paths), delete_errno=-1, kept_remote=True, path_hashes=path_hashes)
    delete_result = baidu.delete_files(remote_paths, async_mode=0)
    return CleanupResult(object_count=len(remote_paths), delete_errno=delete_result.errno, kept_remote=False, path_hashes=path_hashes)


def _resolve_credentials(args: argparse.Namespace):
    token = os.environ.get(args.device_token_env, "").strip()
    return resolve_or_register_device_credentials(cloud_api_base_url=args.base_url, provided_device_token=token)


def _decrypt_selected_token(cloud: BaiduCloudClient, account_id: str, password: str):
    workflow = BaiduAuthWorkflow(cloud)
    actual_account_id = account_id.strip() or _selected_account_id(workflow)
    return workflow.decrypt_password_token(actual_account_id, authorization_password=password)


def _selected_account_id(workflow: BaiduAuthWorkflow) -> str:
    selected = [account for account in workflow.load_accounts() if account.selected]
    if not selected:
        raise ValueError("account_id is required because current device has no selected Baidu account")
    return selected[0].account_id


def _read_authorization_password(password_env: str) -> str:
    password = os.environ.get(password_env, "") if password_env else ""
    if not password:
        password = getpass.getpass("授权/归档密码（不回显，不写入文件）: ")
    if not password:
        raise ValueError("authorization password is required")
    return password


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_error_summary(exc: Exception) -> str:
    if isinstance(exc, CloudAPIError):
        return f"cloud_api_error status={exc.status_code} code={exc.error_code or 'unknown'}"
    if isinstance(exc, BaiduNetdiskError):
        code = exc.error_code or "unknown"
        status = f" status={exc.status_code}" if exc.status_code else ""
        return f"baidu_netdisk_error{status} code={code}"
    if isinstance(exc, ValueError):
        allowed = {
            "account_id is required because current device has no selected Baidu account",
            "authorization password is required",
            "completed job cloud summary mismatch",
            "multipart_bytes must exceed one upload part",
            "real pipeline did not complete",
            "real pipeline final sync reported failures",
            "real pipeline multipart upload did not cross part boundary",
            "real pipeline reconcile did not confirm all remote objects",
            "real pipeline reconcile reported differences",
            "real pipeline sync reported failures",
            "real pipeline upload is missing",
            "remote cleanup did not delete expected objects",
            "remote cleanup did not find expected objects",
            "same-path conflict probe did not detect conflict",
            "small_bytes must be >= 1",
            "verify_limit must be >= 0",
        }
        message = str(exc)
        return message if message in allowed else "invalid_argument"
    if isinstance(exc, sqlite3.Error):
        return "sqlite_error"
    if isinstance(exc, OSError):
        return "filesystem_error"
    return type(exc).__name__


def _print(message: str) -> None:
    print(message, flush=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
