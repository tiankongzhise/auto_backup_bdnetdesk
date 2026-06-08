from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

from auto_backup_client.backup_jobs import BackupJobManager, BackupSourceInput
from auto_backup_client.backup_pipeline import BackupPipeline, BackupPipelineError, BackupPipelineOptions
from auto_backup_client.baidu.auth_workflow import BaiduAuthWorkflow
from auto_backup_client.baidu.cloud_api import BaiduCloudClient, CloudAPIError
from auto_backup_client.baidu.upload import DEFAULT_BACKUP_ROOT_DIR, DEFAULT_PART_SIZE, BaiduNetdiskClient, BaiduNetdiskError
from auto_backup_client.device_credentials import resolve_or_register_device_credentials
from auto_backup_client.settings import ClientSettings
from auto_backup_client.sqlite_store import SQLiteClientStore


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="端到端备份编排入口。默认只执行本地扫描、去重和 7-Zip 归档。")
    parser.add_argument("--base-url", default=os.environ.get("CLOUD_API_BASE_URL", "https://backup.baichengedu.com"))
    parser.add_argument(
        "--device-token-env",
        default="CLOUD_API_DEVICE_TOKEN",
        help="读取 Device Token 的环境变量名；未设置时复用本机 DPAPI 凭据。",
    )
    parser.add_argument("--password-env", default="", help="从指定环境变量读取归档密码；未指定时交互读取。")
    parser.add_argument("--sqlite-path", default="", help="本地 SQLite 路径；默认读取 LOCAL_SQLITE_PATH。")
    parser.add_argument("--cache-root", default="", help="缓存目录；默认读取 LOCAL_CACHE_DIR。")
    parser.add_argument("--skip-cache-budget-check", action="store_true", help="跳过本地缓存有效预算检查。")
    parser.add_argument("--cache-quota-gib", type=float, default=40.0)
    parser.add_argument("--min-effective-cache-gib", type=float, default=40.0)
    parser.add_argument("--max-archive-gib", type=float, default=4.0)
    parser.add_argument("--cleanup-cache-artifacts", action="store_true", help="完成后按缓存等级清理已登记 artifact。")
    parser.add_argument("--cleanup-cache-dry-run", action="store_true", help="只输出缓存清理计划，不实际删除。")
    parser.add_argument("--job-id", default="", help="运行已有 job；省略时必须提供 source 并创建新 job。")
    parser.add_argument("--job-name", default="", help="创建新 job 时使用的任务名。")
    parser.add_argument("--source", action="append", default=[], help="创建新 job 时添加来源，可重复传入。")
    parser.add_argument("--root-dir", default=DEFAULT_BACKUP_ROOT_DIR, help="百度备份根目录，必须位于 /apps/{appname} 下。")
    parser.add_argument("--archive-seq", type=int, default=1)
    parser.add_argument("--part-size-mib", type=int, default=4, choices=(4, 16, 32))
    parser.add_argument("--account-id", default="", help="百度账号 ID；真实上传时省略则使用当前设备已选择账号。")
    parser.add_argument("--upload", action="store_true", help="接入真实百度可恢复上传。")
    parser.add_argument("--skip-quota-check", action="store_true", help="上传时跳过百度容量检查。")
    parser.add_argument("--sync-outbox", action="store_true", help="接入真实云端 Cloud Sync。")
    parser.add_argument("--reconcile-remote", action="store_true", help="上传后调用真实百度 listall 校对远端对象。")
    parser.add_argument("--refresh-cloud-candidates", action="store_true", help="去重后查询真实云端内容候选。")
    parser.add_argument("--no-complete", action="store_true", help="即使上传校对一致也不把 job 标记 completed。")
    parser.add_argument("--sync-batch-size", type=int, default=100)
    parser.add_argument("--max-sync-batches", type=int, default=20)

    args = parser.parse_args(argv)
    try:
        return _run(args)
    except (BackupPipelineError, CloudAPIError, BaiduNetdiskError, ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
        _print(f"操作失败: {_safe_error_summary(exc)}")
        return 1


def _run(args: argparse.Namespace) -> int:
    settings = ClientSettings.from_env()
    sqlite_path = Path(args.sqlite_path.strip() or settings.local_sqlite_path)
    cache_root = Path(args.cache_root.strip() or settings.local_cache_dir)
    store = SQLiteClientStore(sqlite_path)
    store.migrate()
    credentials, source = _resolve_credentials(args) if _needs_cloud_or_upload(args) else (None, "not_required")
    device_id = getattr(credentials, "device_id", "") or "current-device"
    job_id = args.job_id.strip() or _create_job(store, device_id=device_id, sources=args.source, job_name=args.job_name)
    password = _read_archive_password(args.password_env)

    cloud_client = None
    baidu_client = None
    account_id = args.account_id.strip()
    try:
        if _needs_cloud_or_upload(args):
            cloud_client = BaiduCloudClient(args.base_url, credentials.device_token, timeout=30.0)
            if args.upload:
                decrypted = _decrypt_selected_token(cloud_client, account_id, password)
                account_id = decrypted.encrypted.account_id
                baidu_client = BaiduNetdiskClient(decrypted.token.access_token, timeout=120.0)
        pipeline = BackupPipeline(
            store=store,
            device_id=device_id,
            baidu_client=baidu_client,
            cloud_client=cloud_client,
        )
        result = pipeline.run_job(
            job_id,
            BackupPipelineOptions(
                cache_root=cache_root,
                password=password,
                account_id=account_id,
                root_dir=args.root_dir,
                archive_seq=args.archive_seq,
                part_size=args.part_size_mib * 1024 * 1024,
                refresh_cloud_candidates=args.refresh_cloud_candidates,
                run_upload=args.upload,
                check_quota=not args.skip_quota_check,
                sync_outbox=args.sync_outbox,
                reconcile_remote=args.reconcile_remote,
                mark_completed=not args.no_complete,
                sync_batch_size=args.sync_batch_size,
                max_sync_batches=args.max_sync_batches,
                enforce_cache_budget=not args.skip_cache_budget_check,
                cache_quota_bytes=_gib_to_bytes(args.cache_quota_gib),
                min_effective_cache_budget_bytes=_gib_to_bytes(args.min_effective_cache_gib),
                max_archive_size_bytes=_gib_to_bytes(args.max_archive_gib),
                cleanup_cache_artifacts=args.cleanup_cache_artifacts,
                cleanup_cache_dry_run=args.cleanup_cache_dry_run,
            ),
        )
    finally:
        if baidu_client is not None:
            baidu_client.close()
        if cloud_client is not None:
            cloud_client.close()

    _print(f"Device Token 来源: {source}")
    _print(f"job_id: {result.backup_job_id}")
    _print(f"final_stage: {result.final_stage}")
    _print(f"completed: {str(result.completed).lower()}")
    _print(f"scan_files: {result.scan.file_count}")
    _print(f"scan_folders: {result.scan.folder_count}")
    _print(f"scan_issues: {result.scan.issue_count}")
    _print(f"content_references: {result.content_index.reference_count}")
    _print(f"payload_sources: {result.content_index.payload_source_count}")
    _print(f"local_duplicates: {result.content_index.local_duplicate_count}")
    _print(f"archive_id: {result.archive.archive_id}")
    _print(f"archive_sha256: {result.archive.archive_sha256}")
    _print(f"archive_type: {result.archive.archive_type}")
    _print(f"manifest_sha256: {result.archive.manifest_sha256}")
    if result.cache_usage is not None:
        _print(f"cache_level_before: {result.cache_usage.level}")
        _print(f"cache_effective_budget_bytes_before: {result.cache_usage.effective_budget_bytes}")
    if result.cloud_candidates is not None:
        _print(f"cloud_candidates_checked: {result.cloud_candidates.checked_content_count}")
        _print(f"cloud_duplicate_candidates: {result.cloud_candidates.cloud_duplicate_candidate_count}")
    if result.upload is not None:
        _print(f"upload_session_id: {result.upload.upload_session_id}")
        _print(f"remote_archive_path_sha256: {_sha256_text(result.upload.remote_archive_path)}")
        _print(f"remote_meta_path_sha256: {_sha256_text(result.upload.remote_meta_path)}")
        _print(f"remote_job_index_path_sha256: {_sha256_text(result.upload.remote_job_index_path)}")
        _print(f"uploaded_part_count: {len(result.upload.uploaded_partseqs)}")
    if result.sync is not None:
        _print(f"sync_selected: {result.sync.selected}")
        _print(f"sync_synced: {result.sync.synced}")
        _print(f"sync_conflicts: {result.sync.conflicts}")
        _print(f"sync_rejected: {result.sync.rejected}")
        _print(f"sync_retryable: {result.sync.retryable}")
    if result.reconcile is not None:
        for status, count in sorted(result.reconcile.status_counts.items()):
            if count:
                _print(f"reconcile_{status}: {count}")
    if result.cache_cleanup is not None:
        _print(f"cache_cleanup_dry_run: {str(result.cache_cleanup.dry_run).lower()}")
        _print(f"cache_cleanup_selected_count: {result.cache_cleanup.selected_count}")
        _print(f"cache_cleanup_deleted_count: {result.cache_cleanup.deleted_count}")
        _print(f"cache_cleanup_freed_bytes: {result.cache_cleanup.freed_bytes}")
    _print("端到端编排完成")
    return 0


def _create_job(store: SQLiteClientStore, *, device_id: str, sources: Sequence[str], job_name: str) -> str:
    if not sources:
        raise ValueError("source is required when job_id is not provided")
    created = BackupJobManager(store, device_id=device_id).create_job(
        [BackupSourceInput(str(source)) for source in sources],
        job_name=job_name,
    )
    return created.job.backup_job_id


def _needs_cloud_or_upload(args: argparse.Namespace) -> bool:
    return bool(args.upload or args.sync_outbox or args.reconcile_remote or args.refresh_cloud_candidates)


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


def _read_archive_password(password_env: str) -> str:
    password = os.environ.get(password_env, "") if password_env else ""
    if not password:
        password = getpass.getpass("归档密码（不回显，不写入文件）: ")
    if not password:
        raise ValueError("archive password is required")
    return password


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _gib_to_bytes(value: float) -> int:
    if value < 0:
        raise ValueError("GiB value must be >= 0")
    return int(value * 1024 * 1024 * 1024)


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
            "archive password is required",
            "GiB value must be >= 0",
            "source is required when job_id is not provided",
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
