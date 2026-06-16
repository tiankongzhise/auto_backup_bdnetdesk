from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

from auto_backup_client.baidu.cloud_api import BaiduCloudClient, CloudAPIError
from auto_backup_client.device_credentials import resolve_or_register_device_credentials
from auto_backup_client.settings import ClientSettings
from auto_backup_client.sqlite_store import SQLiteClientStore, utc_now_iso
from auto_backup_client.sync_worker import SUCCESS_STATUSES, SyncOutboxWorker


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="同步本地 SQLite sync_outbox 到真实云端 Cloud Sync API。")
    subparsers = parser.add_subparsers(dest="command", required=True)
    sync_parser = subparsers.add_parser("sync-outbox", help="运行一次 sync_outbox 同步。")
    sync_parser.add_argument("--base-url", default=os.environ.get("CLOUD_API_BASE_URL", "https://backup.baichengedu.com"))
    sync_parser.add_argument(
        "--device-token-env",
        default="CLOUD_API_DEVICE_TOKEN",
        help="读取 Device Token 的环境变量名；未设置时复用本机 DPAPI 凭据。",
    )
    sync_parser.add_argument("--sqlite-path", default="", help="本地 SQLite 路径；默认读取 LOCAL_SQLITE_PATH。")
    sync_parser.add_argument("--batch-size", type=int, default=100)
    sync_parser.add_argument("--verify-cloud-summary", action="store_true", help="同步后读取云端 revision 摘要做脱敏校验。")
    sync_parser.add_argument("--verify-limit", type=int, default=20, help="summary 校验最多检查的已发送事件数。")

    args = parser.parse_args(argv)
    try:
        if args.command == "sync-outbox":
            return _sync_outbox(args)
    except (CloudAPIError, ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
        _print(f"操作失败: {_safe_error_summary(exc)}")
        return 1
    return 2


def _sync_outbox(args: argparse.Namespace) -> int:
    if args.batch_size < 1 or args.batch_size > 100:
        raise ValueError("batch_size must be between 1 and 100")
    if args.verify_limit < 0:
        raise ValueError("verify_limit must be >= 0")

    credentials, source = _resolve_credentials(args)
    settings = ClientSettings.from_env()
    sqlite_path = Path(args.sqlite_path.strip() or settings.local_sqlite_path)
    store = SQLiteClientStore(sqlite_path)
    store.migrate()

    selected_before = store.list_outbox_events_for_sync(limit=args.batch_size, now=utc_now_iso())
    with BaiduCloudClient(args.base_url, credentials.device_token, timeout=30.0, device_id=_require_device_id(credentials)) as cloud:
        worker = SyncOutboxWorker(store=store, cloud=cloud, batch_size=args.batch_size)
        result = worker.run_once()
        verified = (
            _verify_cloud_summaries(cloud, selected_before, result.revision_results, limit=args.verify_limit)
            if args.verify_cloud_summary
            else 0
        )

    _print(f"Device Token 来源: {source}")
    _print(f"selected: {result.selected}")
    _print(f"sent: {result.sent}")
    _print(f"synced: {result.synced}")
    _print(f"conflicts: {result.conflicts}")
    _print(f"rejected: {result.rejected}")
    _print(f"retryable: {result.retryable}")
    if args.verify_cloud_summary:
        _print(f"cloud_summary_verified: {verified}")
    return 0


def _verify_cloud_summaries(cloud: BaiduCloudClient, events: Sequence[object], results: Sequence[object], *, limit: int) -> int:
    successful_ids = {result.event_id for result in results if result.status in SUCCESS_STATUSES}
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


def _resolve_credentials(args: argparse.Namespace) -> tuple[object, str]:
    token = os.environ.get(args.device_token_env, "").strip()
    return resolve_or_register_device_credentials(cloud_api_base_url=args.base_url, provided_device_token=token)


def _require_device_id(credentials: object) -> str:
    device_id = str(getattr(credentials, "device_id", "")).strip()
    if not device_id:
        raise ValueError("device_id is required")
    return device_id


def _print(message: str) -> None:
    print(message, flush=True)


def _safe_error_summary(exc: Exception) -> str:
    if isinstance(exc, CloudAPIError):
        return f"cloud_api_error status={exc.status_code} code={exc.error_code or 'unknown'}"
    if isinstance(exc, ValueError):
        message = str(exc)
        allowed = {
            "batch_size must be between 1 and 100",
            "device_id is required",
            "verify_limit must be >= 0",
            "entity_id is required",
        }
        return message if message in allowed else "invalid_argument"
    if isinstance(exc, sqlite3.Error):
        return "sqlite_error"
    if isinstance(exc, OSError):
        return "filesystem_error"
    return type(exc).__name__


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
