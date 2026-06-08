from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

from auto_backup_client.restore_flow import RestoreFlowError, RestoreService
from auto_backup_client.settings import ClientSettings
from auto_backup_client.sqlite_store import SQLiteClientStore


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="恢复流程入口。输出只展示数量、hash 和状态，不打印完整路径。")
    parser.add_argument("--sqlite-path", default="", help="本地 SQLite 路径；默认读取 LOCAL_SQLITE_PATH。")
    parser.add_argument("--cache-root", default="", help="缓存目录；默认读取 LOCAL_CACHE_DIR。")
    parser.add_argument("--device-id", default="current-device")
    parser.add_argument("--job-id", default="")
    parser.add_argument("--keyword", default="")
    parser.add_argument("--password-env", default="", help="从指定环境变量读取归档密码；未指定时交互读取。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="列出可恢复对象候选。")
    restore = subparsers.add_parser("restore", help="执行恢复。")
    restore.add_argument("--content-reference-id", action="append", default=[], help="指定 content_reference_id；可重复。")
    restore.add_argument("--target-mode", choices=("manual_path", "original_path"), default="manual_path")
    restore.add_argument("--target-root", default="", help="manual_path 模式下的恢复根目录。")
    restore.add_argument("--conflict-strategy", choices=("keep_both", "skip_existing"), default="keep_both")

    args = parser.parse_args(argv)
    try:
        return _run(args)
    except (RestoreFlowError, ValueError, OSError, sqlite3.Error) as exc:
        _print(f"操作失败: {_safe_error_summary(exc)}")
        return 1


def _run(args: argparse.Namespace) -> int:
    settings = ClientSettings.from_env()
    sqlite_path = Path(args.sqlite_path.strip() or settings.local_sqlite_path)
    cache_root = Path(args.cache_root.strip() or settings.local_cache_dir)
    store = SQLiteClientStore(sqlite_path)
    store.migrate()
    service = RestoreService(store, device_id=args.device_id or "current-device", cache_root=cache_root)
    if args.command == "list":
        report = service.list_candidates(backup_job_id=args.job_id, keyword=args.keyword, limit=500)
        _print(f"restore_candidates: {len(report.candidates)}")
        _print(f"restore_restorable: {report.restorable_count}")
        _print(f"restore_local_ready: {report.local_ready_count}")
        _print(f"restore_needs_download: {report.needs_download_count}")
        _print(f"restore_blocked: {report.blocked_count}")
        for index, candidate in enumerate(report.candidates[:50], start=1):
            _print(
                " ".join(
                    [
                        f"candidate_{index}_status: {candidate.candidate_status}",
                        f"name={candidate.display_name}",
                        f"content={candidate.content_id[:12]}",
                        f"sha256={candidate.sha256[:12]}",
                        f"archive={candidate.archive_sha256[:12]}",
                        f"path={candidate.path_sha256[:12]}",
                    ]
                )
            )
        return 0
    if args.command == "restore":
        password = _read_archive_password(args.password_env)
        result = service.restore(
            backup_job_id=args.job_id,
            content_reference_ids=tuple(args.content_reference_id),
            target_mode=args.target_mode,
            target_root=args.target_root or None,
            password=password,
            conflict_strategy=args.conflict_strategy,
        )
        _print(f"restore_requested: {result.requested_count}")
        _print(f"restore_restored: {result.restored_count}")
        _print(f"restore_skipped: {result.skipped_count}")
        _print(f"restore_failed: {result.failed_count}")
        for index, item in enumerate(result.results, start=1):
            _print(
                " ".join(
                    [
                        f"result_{index}_status: {item.status}",
                        f"record={item.restore_record_id}",
                        f"target={item.target_path_sha256[:12]}",
                        f"final={item.final_path_sha256[:12]}",
                        f"archive_source={item.archive_source}",
                    ]
                )
            )
        return 0
    raise ValueError("unsupported command")


def _read_archive_password(password_env: str) -> str:
    password = os.environ.get(password_env, "") if password_env else ""
    if not password:
        password = getpass.getpass("归档密码（不回显，不写入文件）: ")
    if not password:
        raise RestoreFlowError("archive password is required")
    return password


def _safe_error_summary(exc: Exception) -> str:
    if isinstance(exc, ValueError):
        allowed = {
            "archive password is required",
            "target_root is required for manual_path restore",
            "unsupported restore conflict strategy",
            "unsupported restore target mode",
            "no restore candidates selected",
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


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
