from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

from auto_backup_client.baidu.auth_workflow import BaiduAuthWorkflow
from auto_backup_client.baidu.cloud_api import BaiduCloudClient, CloudAPIError
from auto_backup_client.baidu.reconcile import (
    RECONCILE_STATUSES,
    RemoteObjectReconciler,
    RemoteReconcileReport,
    RemoteReconcileScope,
    RequestRateLimiter,
)
from auto_backup_client.baidu.reconcile_repair import (
    CONFIRM_REPAIR_TEXT,
    RemoteObjectRepairer,
    RemoteRepairPlan,
    RemoteRepairResult,
    action_filter_from_cli,
    build_remote_repair_plan,
)
from auto_backup_client.baidu.upload import BaiduNetdiskClient, BaiduNetdiskError
from auto_backup_client.device_credentials import resolve_or_register_device_credentials
from auto_backup_client.settings import ClientSettings
from auto_backup_client.sqlite_store import SQLiteClientStore


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="百度远端对象只读校对与脱敏报告。")
    parser.add_argument("--base-url", default=os.environ.get("CLOUD_API_BASE_URL", "https://backup.baichengedu.com"))
    parser.add_argument(
        "--device-token-env",
        default="CLOUD_API_DEVICE_TOKEN",
        help="读取 Device Token 的环境变量名；未设置时复用本机 DPAPI 凭据。",
    )
    parser.add_argument("--password-env", default="", help="从指定环境变量读取授权密码。")
    parser.add_argument("--sqlite-path", default="", help="本地 SQLite 路径；默认读取 LOCAL_SQLITE_PATH。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    remote_parser = subparsers.add_parser("remote-objects", help="校对本地 remote_objects 与百度远端对象。")
    _add_common_subcommand_options(remote_parser)
    remote_parser.add_argument("account_id", nargs="?", default="", help="账号 ID；省略时使用当前设备已选择账号。")
    scope_group = remote_parser.add_mutually_exclusive_group(required=True)
    scope_group.add_argument("--job-id", default="", help="按 job_id 校对。")
    scope_group.add_argument("--upload-session-id", default="", help="按 upload_session_id 校对。")
    scope_group.add_argument("--remote-dir", default="", help="按远端目录校对。")
    remote_parser.add_argument("--non-recursive", action="store_true", help="使用 list 非递归校对；默认使用 listall。")
    remote_parser.add_argument("--page-limit", type=int, default=1000)
    remote_parser.add_argument("--max-requests-per-minute", type=int, default=8)
    remote_parser.add_argument("--show-findings", type=int, default=50, help="最多输出的 finding 数量。")

    repair_parser = subparsers.add_parser("repair-remote-objects", help="基于校对报告生成人工修复候选动作。")
    _add_common_subcommand_options(repair_parser)
    repair_parser.add_argument("account_id", nargs="?", default="", help="账号 ID；省略时使用当前设备已选择账号。")
    repair_scope_group = repair_parser.add_mutually_exclusive_group(required=True)
    repair_scope_group.add_argument("--job-id", default="", help="按 job_id 校对并生成修复候选。")
    repair_scope_group.add_argument("--upload-session-id", default="", help="按 upload_session_id 校对并生成修复候选。")
    repair_scope_group.add_argument("--remote-dir", default="", help="按远端目录校对并生成修复候选。")
    repair_parser.add_argument("--non-recursive", action="store_true", help="使用 list 非递归校对；默认使用 listall。")
    repair_parser.add_argument("--page-limit", type=int, default=1000)
    repair_parser.add_argument("--max-requests-per-minute", type=int, default=8)
    repair_parser.add_argument("--show-candidates", type=int, default=50, help="最多输出的修复候选数量。")
    repair_parser.add_argument(
        "--repair-action",
        default="safe-local",
        choices=("safe-local", "mark_remote_missing", "accept_baidu_metadata"),
        help="选择可写修复动作；默认 safe-local 包含本地标记缺失和接受百度元数据。",
    )
    repair_parser.add_argument("--apply", action="store_true", help="实际写入 SQLite 和 sync_outbox；默认只 dry-run。")
    repair_parser.add_argument(
        "--confirm",
        default="",
        help=f"实际写入时必须填写 {CONFIRM_REPAIR_TEXT}。",
    )

    args = parser.parse_args(argv)
    try:
        if args.command == "remote-objects":
            return _remote_objects(args)
        if args.command == "repair-remote-objects":
            return _repair_remote_objects(args)
    except (CloudAPIError, BaiduNetdiskError, ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
        _print(f"操作失败: {_safe_error_summary(exc)}")
        return 1
    return 2


def _add_common_subcommand_options(command_parser: argparse.ArgumentParser) -> None:
    command_parser.add_argument("--base-url", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    command_parser.add_argument("--device-token-env", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    command_parser.add_argument("--password-env", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    command_parser.add_argument("--sqlite-path", default=argparse.SUPPRESS, help=argparse.SUPPRESS)


def _remote_objects(args: argparse.Namespace) -> int:
    report, source, account_id, _credentials, _store = _run_reconcile_from_args(args)
    _print_report(report, credential_source=source, account_id=account_id, limit=args.show_findings)
    return 0


def _repair_remote_objects(args: argparse.Namespace) -> int:
    if args.show_candidates < 0:
        raise ValueError("show_candidates must be >= 0")
    if args.apply and args.confirm != CONFIRM_REPAIR_TEXT:
        raise ValueError("repair confirmation is required")

    report, source, account_id, credentials, store = _run_reconcile_from_args(args)
    plan = build_remote_repair_plan(report, action_filter=action_filter_from_cli(args.repair_action))
    dry_run = not args.apply
    result = RemoteObjectRepairer(
        store=store,
        updated_by_device_id=_require_device_id(credentials),
    ).apply(plan, dry_run=dry_run)

    _print_repair_plan(
        plan,
        result,
        credential_source=source,
        account_id=account_id,
        limit=args.show_candidates,
    )
    return 0


def _run_reconcile_from_args(args: argparse.Namespace):
    if args.page_limit < 1:
        raise ValueError("page_limit must be >= 1")
    if args.max_requests_per_minute < 1:
        raise ValueError("max_requests_per_minute must be >= 1")
    if getattr(args, "show_findings", 0) < 0:
        raise ValueError("show_findings must be >= 0")

    password = _read_authorization_password(args.password_env)
    credentials, source = _resolve_credentials(args)
    settings = ClientSettings.from_env()
    sqlite_path = Path(args.sqlite_path.strip() or settings.local_sqlite_path)
    store = SQLiteClientStore(sqlite_path)
    store.migrate()
    scope = RemoteReconcileScope(
        job_id=args.job_id,
        upload_session_id=args.upload_session_id,
        remote_dir=args.remote_dir,
        recursive=not args.non_recursive,
        page_limit=args.page_limit,
    )

    with BaiduCloudClient(args.base_url, credentials.device_token, timeout=30.0, device_id=credentials.device_id) as cloud:
        decrypted = _decrypt_selected_token(cloud, args.account_id, password)
    with BaiduNetdiskClient(decrypted.token.access_token, timeout=120.0) as baidu:
        report = RemoteObjectReconciler(
            store=store,
            baidu=baidu,
            rate_limiter=RequestRateLimiter(max_requests_per_minute=args.max_requests_per_minute),
        ).reconcile(scope)
    return report, source, decrypted.encrypted.account_id, credentials, store


def _print_report(
    report: RemoteReconcileReport,
    *,
    credential_source: str,
    account_id: str,
    limit: int,
) -> None:
    _print(f"Device Token 来源: {credential_source}")
    _print(f"account_id_sha256: {_sha256_text(account_id)}")
    _print(f"scope_type: {report.scope.scope_type}")
    _print(f"scope_value_sha256: {_sha256_text(report.scope.scope_value)}")
    _print(f"recursive: {str(report.scope.recursive).lower()}")
    _print(f"local_object_count: {report.local_object_count}")
    _print(f"remote_object_count: {report.remote_object_count}")
    _print(f"finding_count: {len(report.findings)}")
    for status in sorted(RECONCILE_STATUSES):
        _print(f"status_{status}: {report.status_counts.get(status, 0)}")
    for index, finding in enumerate(report.findings[:limit], start=1):
        _print(f"finding_{index}_status: {finding.status}")
        _print(f"finding_{index}_object_type: {finding.object_type}")
        _print(f"finding_{index}_remote_path_sha256: {_sha256_text(finding.remote_path)}")
        _print(f"finding_{index}_suggestion: {finding.suggestion}")
        if finding.local_size is not None or finding.remote_size is not None:
            _print(f"finding_{index}_size: {finding.local_size if finding.local_size is not None else ''}->{finding.remote_size if finding.remote_size is not None else ''}")
        if finding.local_md5 or finding.remote_md5:
            _print(f"finding_{index}_md5: {finding.local_md5}->{finding.remote_md5}")
        if finding.local_fs_id is not None or finding.remote_fs_id is not None:
            _print(f"finding_{index}_fs_id: {finding.local_fs_id if finding.local_fs_id is not None else ''}->{finding.remote_fs_id if finding.remote_fs_id is not None else ''}")
        if finding.error_code:
            _print(f"finding_{index}_error_code: {finding.error_code}")
    if limit < len(report.findings):
        _print(f"finding_omitted_count: {len(report.findings) - limit}")
    _print("远端对象校对完成: read-only report")


def _print_repair_plan(
    plan: RemoteRepairPlan,
    result: RemoteRepairResult,
    *,
    credential_source: str,
    account_id: str,
    limit: int,
) -> None:
    _print(f"Device Token 来源: {credential_source}")
    _print(f"account_id_sha256: {_sha256_text(account_id)}")
    _print(f"scope_type: {plan.report.scope.scope_type}")
    _print(f"scope_value_sha256: {_sha256_text(plan.report.scope.scope_value)}")
    _print(f"recursive: {str(plan.report.scope.recursive).lower()}")
    _print(f"dry_run: {str(result.dry_run).lower()}")
    _print(f"candidate_count: {result.candidate_count}")
    _print(f"writable_count: {result.writable_count}")
    _print(f"selected_count: {result.selected_count}")
    _print(f"applied_count: {result.applied_count}")
    for index, candidate in enumerate(plan.candidates[:limit], start=1):
        _print(f"candidate_{index}_status: {candidate.status}")
        _print(f"candidate_{index}_action: {candidate.action}")
        _print(f"candidate_{index}_object_type: {candidate.object_type}")
        _print(f"candidate_{index}_remote_path_sha256: {_sha256_text(candidate.remote_path)}")
        _print(f"candidate_{index}_selected: {str(candidate.selected).lower()}")
        _print(f"candidate_{index}_will_write: {str(candidate.will_write).lower()}")
        _print(f"candidate_{index}_reason: {candidate.reason}")
        if candidate.local_remote_object_id:
            _print(f"candidate_{index}_local_remote_object_id_sha256: {_sha256_text(candidate.local_remote_object_id)}")
        if "status" in candidate.updates:
            _print(f"candidate_{index}_update_status: {candidate.updates['status']}")
        if "size_bytes" in candidate.updates:
            _print(f"candidate_{index}_update_size_bytes: {candidate.updates['size_bytes']}")
        if "md5" in candidate.updates:
            _print(f"candidate_{index}_update_md5: {candidate.updates['md5']}")
        if "fs_id" in candidate.updates:
            _print(f"candidate_{index}_update_fs_id: {candidate.updates['fs_id']}")
    if limit < len(plan.candidates):
        _print(f"candidate_omitted_count: {len(plan.candidates) - limit}")
    for index, applied in enumerate(result.applied_records, start=1):
        _print(f"applied_{index}_remote_object_id_sha256: {_sha256_text(applied.local_remote_object_id)}")
        _print(f"applied_{index}_action: {applied.action}")
        _print(f"applied_{index}_data_version: {applied.data_version}")
    _print("远端对象人工修复入口完成")


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
        message = str(exc)
        allowed = {
            "account_id is required because current device has no selected Baidu account",
            "authorization password is required",
            "device_id is required",
            "exactly one of job_id, upload_session_id, or remote_dir is required",
            "max_requests_per_minute must be >= 1",
            "page_limit must be >= 1",
            "repair action is invalid",
            "repair confirmation is required",
            "show_candidates must be >= 0",
            "show_findings must be >= 0",
        }
        return message if message in allowed else "invalid_argument"
    if isinstance(exc, sqlite3.Error):
        return "sqlite_error"
    if isinstance(exc, OSError):
        return "filesystem_error"
    return type(exc).__name__


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
