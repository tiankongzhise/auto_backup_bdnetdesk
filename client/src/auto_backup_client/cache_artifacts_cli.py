from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

from auto_backup_client.cache_artifacts import CacheArtifactError, CacheArtifactManager, CacheBudget
from auto_backup_client.settings import ClientSettings
from auto_backup_client.sqlite_store import SQLiteClientStore


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="缓存 artifact 状态和清理入口。输出仅包含数量、大小和路径 hash。")
    parser.add_argument("--sqlite-path", default="", help="本地 SQLite 路径；默认读取 LOCAL_SQLITE_PATH。")
    parser.add_argument("--cache-root", default="", help="缓存目录；默认读取 LOCAL_CACHE_DIR。")
    parser.add_argument("--cache-quota-gib", type=float, default=40.0)
    parser.add_argument("--min-effective-gib", type=float, default=40.0)
    parser.add_argument("--max-archive-gib", type=float, default=4.0)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="统计缓存占用、可释放大小和缓存等级。")
    cleanup = subparsers.add_parser("cleanup", help="清理已登记且允许删除的缓存 artifact。")
    cleanup.add_argument("--job-id", default="")
    cleanup.add_argument("--stage", default="completed")
    cleanup.add_argument("--level", default="", choices=("", "sufficient", "medium", "tight", "critical"))
    cleanup.add_argument("--apply", action="store_true", help="实际删除；默认 dry-run。")

    args = parser.parse_args(argv)
    try:
        return _run(args)
    except (CacheArtifactError, ValueError, OSError, sqlite3.Error) as exc:
        _print(f"操作失败: {_safe_error_summary(exc)}")
        return 1


def _run(args: argparse.Namespace) -> int:
    settings = ClientSettings.from_env()
    sqlite_path = Path(args.sqlite_path.strip() or settings.local_sqlite_path)
    cache_root = Path(args.cache_root.strip() or settings.local_cache_dir)
    store = SQLiteClientStore(sqlite_path)
    store.migrate()
    manager = CacheArtifactManager(store, cache_root=cache_root)
    budget = CacheBudget(
        cache_root=cache_root,
        cache_quota_bytes=_gib_to_bytes(args.cache_quota_gib),
        min_effective_budget_bytes=_gib_to_bytes(args.min_effective_gib),
        max_archive_size_bytes=_gib_to_bytes(args.max_archive_gib),
    )
    if args.command == "status":
        usage = manager.usage(budget)
        _print(f"cache_level: {usage.level}")
        _print(f"cache_used_bytes: {usage.used_bytes}")
        _print(f"cache_active_artifact_bytes: {usage.active_bytes}")
        _print(f"cache_releasable_bytes: {usage.releasable_bytes}")
        _print(f"cache_disk_free_bytes: {usage.disk_free_bytes}")
        _print(f"cache_effective_budget_bytes: {usage.effective_budget_bytes}")
        _print(f"cache_can_start_new_job: {str(usage.can_start_new_job).lower()}")
        if usage.reason:
            _print(f"cache_block_reason: {usage.reason}")
        return 0
    if args.command == "cleanup":
        level = args.level or manager.usage(budget).level
        result = manager.cleanup(
            current_stage=args.stage,
            cache_level=level,
            dry_run=not args.apply,
            job_id=args.job_id,
        )
        _print(f"cleanup_dry_run: {str(result.dry_run).lower()}")
        _print(f"cleanup_selected_count: {result.selected_count}")
        _print(f"cleanup_deleted_count: {result.deleted_count}")
        _print(f"cleanup_freed_bytes: {result.freed_bytes}")
        for index, path_hash in enumerate(result.path_sha256s, start=1):
            _print(f"cleanup_path_{index}_sha256: {path_hash}")
        return 0
    raise ValueError("unsupported command")


def _gib_to_bytes(value: float) -> int:
    if value < 0:
        raise ValueError("GiB value must be >= 0")
    return int(value * 1024 * 1024 * 1024)


def _safe_error_summary(exc: Exception) -> str:
    if isinstance(exc, ValueError):
        message = str(exc)
        allowed = {
            "cache artifact path must stay inside cache root",
            "GiB value must be >= 0",
        }
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
