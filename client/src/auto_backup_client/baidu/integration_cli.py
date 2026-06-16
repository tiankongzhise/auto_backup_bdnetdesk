from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import sqlite3
import sys
import tempfile
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from auto_backup_client.baidu.auth_workflow import BaiduAuthWorkflow
from auto_backup_client.baidu.cloud_api import BaiduCloudClient, CloudAPIError
from auto_backup_client.baidu.resumable_upload import BaiduResumableUploader, ResumableArchiveInput
from auto_backup_client.baidu.upload import (
    DEFAULT_BACKUP_ROOT_DIR,
    BaiduNetdiskClient,
    BaiduNetdiskError,
    compute_file_block_plan,
)
from auto_backup_client.device_credentials import resolve_or_register_device_credentials
from auto_backup_client.settings import ClientSettings
from auto_backup_client.sqlite_store import SQLiteClientStore, utc_now_iso
from auto_backup_client.sync_worker import SUCCESS_STATUSES, SyncOutboxWorker, SyncWorkerResult


DEFAULT_ARCHIVE_SIZE_BYTES = 1024 * 1024 + 17


@dataclass(frozen=True)
class CleanupResult:
    object_count: int
    delete_errno: int = -1
    kept_remote: bool = False
    path_hashes: tuple[str, ...] = tuple()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="真实 upload-resumable 联调与脱敏清理入口。")
    parser.add_argument("--base-url", default=os.environ.get("CLOUD_API_BASE_URL", "https://backup.baichengedu.com"))
    parser.add_argument(
        "--device-token-env",
        default="CLOUD_API_DEVICE_TOKEN",
        help="读取 Device Token 的环境变量名；未设置时复用本机 DPAPI 凭据。",
    )
    parser.add_argument("--password-env", default="", help="从指定环境变量读取授权密码。")
    parser.add_argument("--sqlite-path", default="", help="本地 SQLite 路径；默认读取 LOCAL_SQLITE_PATH。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run-resumable", help="执行真实 upload-resumable -> sync-outbox -> summary -> delete 联调。")
    _add_common_subcommand_options(run_parser)
    run_parser.add_argument("account_id", nargs="?", default="", help="账号 ID；省略时使用当前设备已选择账号。")
    run_parser.add_argument("--root-dir", default=DEFAULT_BACKUP_ROOT_DIR, help="百度备份根目录，必须位于 /apps/{appname} 下。")
    run_parser.add_argument("--device-id", default="", help="远端路径中的 device_id；默认使用本机云端 device_id。")
    run_parser.add_argument("--job-id", default="", help="远端路径中的 job_id；默认生成 integration-resumable-*。")
    run_parser.add_argument("--archive-seq", type=int, default=1)
    run_parser.add_argument("--archive-type", default="payload", choices=("payload", "manifest_only", "mixed"))
    run_parser.add_argument("--manifest-id", default="")
    run_parser.add_argument("--part-size-mib", type=int, default=4, choices=(4, 16, 32))
    run_parser.add_argument("--archive-size-bytes", type=int, default=DEFAULT_ARCHIVE_SIZE_BYTES)
    run_parser.add_argument("--work-dir", default="", help="生成本地临时 archive 的目录；默认使用系统临时目录。")
    run_parser.add_argument("--skip-quota-check", action="store_true", help="跳过容量检查；默认检查真实百度容量。")
    run_parser.add_argument("--no-verify-cloud-summary", action="store_true", help="跳过云端 revision summary 校验；默认校验。")
    run_parser.add_argument("--verify-limit", type=int, default=20, help="summary 校验最多检查的已发送事件数。")
    run_parser.add_argument("--batch-size", type=int, default=100)
    run_parser.add_argument("--keep-remote", action="store_true", help="保留远端测试对象，默认上传同步后删除。")

    cleanup_parser = subparsers.add_parser("cleanup-resumable", help="按 job_id 或 upload_session_id 清理本地账本中的远端对象。")
    _add_common_subcommand_options(cleanup_parser)
    cleanup_parser.add_argument("account_id", nargs="?", default="", help="账号 ID；省略时使用当前设备已选择账号。")
    cleanup_group = cleanup_parser.add_mutually_exclusive_group(required=True)
    cleanup_group.add_argument("--job-id", default="", help="按 job_id 查询本地 remote_objects。")
    cleanup_group.add_argument("--upload-session-id", default="", help="按 upload_session_id 查询所属 job 的 remote_objects。")

    args = parser.parse_args(argv)
    try:
        if args.command == "run-resumable":
            return _run_resumable(args)
        if args.command == "cleanup-resumable":
            return _cleanup_resumable(args)
    except (CloudAPIError, BaiduNetdiskError, ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
        _print(f"操作失败: {_safe_error_summary(exc)}")
        return 1
    return 2


def _add_common_subcommand_options(command_parser: argparse.ArgumentParser) -> None:
    command_parser.add_argument("--base-url", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    command_parser.add_argument("--device-token-env", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    command_parser.add_argument("--password-env", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    command_parser.add_argument("--sqlite-path", default=argparse.SUPPRESS, help=argparse.SUPPRESS)


def _run_resumable(args: argparse.Namespace) -> int:
    if args.batch_size < 1 or args.batch_size > 100:
        raise ValueError("batch_size must be between 1 and 100")
    if args.verify_limit < 0:
        raise ValueError("verify_limit must be >= 0")
    if args.archive_size_bytes < 1:
        raise ValueError("archive_size_bytes must be >= 1")

    password = _read_authorization_password(args.password_env)
    credentials, source = _resolve_credentials(args)
    settings = ClientSettings.from_env()
    sqlite_path = Path(args.sqlite_path.strip() or settings.local_sqlite_path)
    store = SQLiteClientStore(sqlite_path)
    store.migrate()
    job_id = args.job_id.strip() or f"integration-resumable-{uuid.uuid4().hex[:12]}"
    device_id = _device_id_arg(args.device_id, credentials)

    temp_parent = Path(args.work_dir) if args.work_dir else None
    if temp_parent is not None:
        temp_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="auto-backup-resumable-", dir=str(temp_parent) if temp_parent else None) as temp_dir:
        archive_path = Path(temp_dir) / "integration-archive.7z"
        _write_temp_archive(archive_path, args.archive_size_bytes)
        plan = compute_file_block_plan(archive_path, part_size=args.part_size_mib * 1024 * 1024)

        with BaiduCloudClient(args.base_url, credentials.device_token, timeout=30.0) as cloud:
            decrypted = _decrypt_selected_token(cloud, args.account_id, password)
            with BaiduNetdiskClient(decrypted.token.access_token, timeout=120.0) as baidu:
                if not args.skip_quota_check:
                    quota = baidu.get_quota()
                    if quota.available < plan.size:
                        raise BaiduNetdiskError("baidu netdisk available quota is smaller than generated archive", error_code="quota_not_enough")
                uploader = BaiduResumableUploader(store=store, baidu=baidu, updated_by_device_id=device_id)
                upload = uploader.upload(
                    ResumableArchiveInput(
                        local_path=archive_path,
                        job_id=job_id,
                        device_id=device_id,
                        account_id=decrypted.encrypted.account_id,
                        archive_seq=args.archive_seq,
                        archive_type=args.archive_type,
                        manifest_id=args.manifest_id,
                        root_dir=args.root_dir,
                        part_size=args.part_size_mib * 1024 * 1024,
                    )
                )
                selected_before = store.list_outbox_events_for_sync(limit=args.batch_size, now=utc_now_iso())
                sync_result = SyncOutboxWorker(store=store, cloud=cloud, batch_size=args.batch_size).run_once()
                verified = (
                    _verify_cloud_summaries(cloud, selected_before, sync_result, limit=args.verify_limit)
                    if not args.no_verify_cloud_summary
                    else 0
                )
                cleanup = _cleanup_remote_objects(store, baidu, job_id=job_id, upload_session_id="", keep_remote=args.keep_remote)

    _print(f"Device Token 来源: {source}")
    _print(f"account_id: {decrypted.encrypted.account_id}")
    _print(f"token_version: {decrypted.encrypted.token_version}")
    _print(f"job_id: {job_id}")
    _print(f"upload_session_id: {upload.upload_session_id}")
    _print(f"archive_sha256: {upload.archive_sha256}")
    _print(f"part_count: {len(plan.parts)}")
    _print(f"uploaded_part_count: {len(upload.uploaded_partseqs)}")
    _print(f"reused_uploadid: {upload.reused_uploadid}")
    _print(f"archive_fs_id: {upload.created.fs_id}")
    _print(f"meta_fs_id: {upload.meta_created.fs_id}")
    _print(f"job_index_fs_id: {upload.job_index_created.fs_id}")
    _print(f"remote_archive_path_sha256: {_sha256_text(upload.remote_archive_path)}")
    _print(f"remote_meta_path_sha256: {_sha256_text(upload.remote_meta_path)}")
    _print(f"remote_job_index_path_sha256: {_sha256_text(upload.remote_job_index_path)}")
    _print(f"sync_selected: {sync_result.selected}")
    _print(f"sync_sent: {sync_result.sent}")
    _print(f"sync_synced: {sync_result.synced}")
    _print(f"sync_conflicts: {sync_result.conflicts}")
    _print(f"sync_rejected: {sync_result.rejected}")
    _print(f"sync_retryable: {sync_result.retryable}")
    if not args.no_verify_cloud_summary:
        _print(f"cloud_summary_verified: {verified}")
    _print(f"cleanup_object_count: {cleanup.object_count}")
    if cleanup.kept_remote:
        _print("cleanup_kept_remote: true")
    else:
        _print(f"cleanup_delete_errno: {cleanup.delete_errno}")
    for index, path_hash in enumerate(cleanup.path_hashes, start=1):
        _print(f"cleanup_path_{index}_sha256: {path_hash}")
    _print("真实联调完成: upload-resumable -> sync-outbox -> cloud-summary -> filemanager/delete")
    return 0


def _cleanup_resumable(args: argparse.Namespace) -> int:
    password = _read_authorization_password(args.password_env)
    credentials, source = _resolve_credentials(args)
    settings = ClientSettings.from_env()
    sqlite_path = Path(args.sqlite_path.strip() or settings.local_sqlite_path)
    store = SQLiteClientStore(sqlite_path)
    store.migrate()
    with BaiduCloudClient(args.base_url, credentials.device_token, timeout=30.0) as cloud:
        decrypted = _decrypt_selected_token(cloud, args.account_id, password)
    with BaiduNetdiskClient(decrypted.token.access_token, timeout=120.0) as baidu:
        cleanup = _cleanup_remote_objects(
            store,
            baidu,
            job_id=args.job_id,
            upload_session_id=args.upload_session_id,
            keep_remote=False,
        )

    _print(f"Device Token 来源: {source}")
    _print(f"account_id: {decrypted.encrypted.account_id}")
    _print(f"token_version: {decrypted.encrypted.token_version}")
    _print(f"cleanup_object_count: {cleanup.object_count}")
    _print(f"cleanup_delete_errno: {cleanup.delete_errno}")
    for index, path_hash in enumerate(cleanup.path_hashes, start=1):
        _print(f"cleanup_path_{index}_sha256: {path_hash}")
    _print("远端临时对象清理完成: filemanager/delete")
    return 0


def _cleanup_remote_objects(
    store: SQLiteClientStore,
    baidu: BaiduNetdiskClient,
    *,
    job_id: str,
    upload_session_id: str,
    keep_remote: bool,
) -> CleanupResult:
    rows = store.list_remote_objects_for_cleanup(job_id=job_id, upload_session_id=upload_session_id)
    remote_paths = tuple(str(row["remote_path"]) for row in rows if str(row.get("remote_path", "")).strip())
    path_hashes = tuple(_sha256_text(path) for path in remote_paths)
    if not remote_paths:
        return CleanupResult(object_count=0, delete_errno=-1, kept_remote=keep_remote, path_hashes=path_hashes)
    if keep_remote:
        return CleanupResult(object_count=len(remote_paths), delete_errno=-1, kept_remote=True, path_hashes=path_hashes)
    delete_result = baidu.delete_files(remote_paths, async_mode=0)
    return CleanupResult(object_count=len(remote_paths), delete_errno=delete_result.errno, kept_remote=False, path_hashes=path_hashes)


def _verify_cloud_summaries(cloud: BaiduCloudClient, events: Sequence[object], result: SyncWorkerResult, *, limit: int) -> int:
    successful_ids = {item.event_id for item in result.revision_results if item.status in SUCCESS_STATUSES}
    verified = 0
    for event in [item for item in events if item.event_id in successful_ids][:limit]:
        summary = cloud.get_entity_summary(event.entity_id)
        if _summary_matches_event(summary, event):
            verified += 1
    return verified


def _summary_matches_event(summary: object, event: object) -> bool:
    if (
        summary.revision_id == event.revision_id
        and summary.data_version == event.data_version
        and summary.canonical_record_sha256 == event.canonical_record_sha256
    ):
        return True
    return any(
        revision.revision_id == event.revision_id
        and revision.data_version == event.data_version
        and revision.canonical_record_sha256 == event.canonical_record_sha256
        for revision in summary.recent_revisions
    )


def _decrypt_selected_token(cloud: BaiduCloudClient, account_id: str, password: str):
    workflow = BaiduAuthWorkflow(cloud)
    actual_account_id = account_id.strip() or _selected_account_id(workflow)
    return workflow.decrypt_password_token(actual_account_id, authorization_password=password)


def _selected_account_id(workflow: BaiduAuthWorkflow) -> str:
    selected = [account for account in workflow.load_accounts() if account.selected]
    if not selected:
        raise ValueError("account_id is required because current device has no selected Baidu account")
    return selected[0].account_id


def _resolve_credentials(args: argparse.Namespace) -> tuple[object, str]:
    token = os.environ.get(args.device_token_env, "").strip()
    return resolve_or_register_device_credentials(cloud_api_base_url=args.base_url, provided_device_token=token)


def _device_id_arg(value: str, credentials: object) -> str:
    explicit = value.strip()
    return explicit or _require_device_id(credentials)


def _require_device_id(credentials: object) -> str:
    device_id = str(getattr(credentials, "device_id", "")).strip()
    if not device_id:
        raise ValueError("device_id is required")
    return device_id


def _read_authorization_password(password_env: str) -> str:
    password = os.environ.get(password_env, "") if password_env else ""
    if not password:
        password = getpass.getpass("授权密码（不回显，不写入文件）: ")
    if not password:
        raise ValueError("authorization password is required")
    return password


def _write_temp_archive(path: Path, size_bytes: int) -> None:
    chunk = b"auto-backup-resumable-integration\n"
    remaining = size_bytes
    with path.open("wb") as handle:
        while remaining > 0:
            block = chunk[:remaining] if remaining < len(chunk) else chunk
            handle.write(block)
            remaining -= len(block)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _print(message: str) -> None:
    print(message, flush=True)


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
            "archive_size_bytes must be >= 1",
            "authorization password is required",
            "batch_size must be between 1 and 100",
            "device_id is required",
            "exactly one of job_id or upload_session_id is required",
            "verify_limit must be >= 0",
        }
        message = str(exc)
        return message if message in allowed else "invalid_argument"
    if isinstance(exc, sqlite3.Error):
        return "sqlite_error"
    if isinstance(exc, OSError):
        return "filesystem_error"
    return type(exc).__name__


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
