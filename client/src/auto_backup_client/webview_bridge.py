from __future__ import annotations

import os
import secrets
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from auto_backup_client.backup_history_sync import DeviceBackupHistoryRefresher
from auto_backup_client.backup_jobs import (
    BackupJobManager,
    BackupSourceInput,
    detect_source_type,
    normalize_sources,
    path_sha256,
    status_label,
)
from auto_backup_client.backup_pipeline import BackupPipeline, BackupPipelineOptions
from auto_backup_client.baidu.auth_workflow import BaiduAuthWorkflow
from auto_backup_client.baidu.cloud_api import BaiduCloudClient
from auto_backup_client.baidu.kdf_store import PasswordKDFStore
from auto_backup_client.baidu.reconcile import RemoteObjectReconciler, RemoteReconcileScope
from auto_backup_client.baidu.reconcile_repair import CONFIRM_REPAIR_TEXT, RemoteObjectRepairer, build_remote_repair_plan
from auto_backup_client.baidu.upload import DEFAULT_BACKUP_ROOT_DIR, DEFAULT_PART_SIZE, BaiduNetdiskClient
from auto_backup_client.device_credentials import resolve_or_register_device_credentials
from auto_backup_client.restore_flow import BaiduArchiveDownloader, RestoreService
from auto_backup_client.settings import ClientSettings
from auto_backup_client.source_cleanup import (
    CLEANUP_CONFIRM_TEXT,
    PERMANENT_DELETE_CONFIRM_TEXT,
    SourceCleanupService,
)
from auto_backup_client.source_mapping import SourceMappingQuery
from auto_backup_client.sqlite_store import SQLiteClientStore


BridgeCallable = Callable[[], dict[str, Any]]
OperationCallable = Callable[["OperationHandle"], dict[str, Any]]
DeviceCredentialResolver = Callable[..., tuple[Any, str]]
DEFAULT_MAX_ARCHIVE_SIZE_BYTES = 4 * 1024**3


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short_digest(value: str | None, *, length: int = 12) -> str | None:
    if not value:
        return None
    return value[:length]


def _edge_hint(value: str | None) -> str:
    if not value:
        return ""
    normalized = str(value)
    if len(normalized) <= 8:
        return _short_digest(path_sha256(normalized), length=8) or ""
    return f"{normalized[:4]}...{normalized[-4:]}"


def _device_id_hint(value: str | None) -> str:
    if not value:
        return ""
    normalized = str(value).strip()
    if not normalized:
        return ""
    if len(normalized) <= 18:
        return normalized
    if normalized.startswith("dev_"):
        return f"{normalized[:12]}...{normalized[-4:]}"
    return _edge_hint(normalized)


def _status_label(status: str) -> str:
    labels = {
        "queued": "待开始",
        "running": "运行中",
        "paused": "已暂停",
        "canceled": "已取消",
        "completed": "已完成",
        "failed_retryable": "等待重试",
        "failed_terminal": "失败",
    }
    return labels.get(status, status_label(status))


def _size_label(size: int | None) -> str:
    if size is None:
        return "-"
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)
    unit_index = 0
    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{int(value)} {units[unit_index]}"
    return f"{value:.1f} {units[unit_index]}"


def _safe_error(exc: BaseException) -> dict[str, Any]:
    message = str(exc).strip() or exc.__class__.__name__
    if "\\" in message or ":/" in message or "token" in message.lower() or "password" in message.lower():
        message = f"{exc.__class__.__name__}: 操作失败，详细信息已在前端脱敏"
    return {
        "type": exc.__class__.__name__,
        "message": message[:300],
    }


def _operation_error(exc: BaseException) -> dict[str, Any]:
    data = _safe_error(exc)
    data["trace"] = traceback.format_exc(limit=8)
    return data


def _as_source_inputs(sources: Iterable[Any]) -> list[BackupSourceInput]:
    normalized: list[BackupSourceInput] = []
    for item in sources:
        if isinstance(item, dict):
            raw_path = str(item.get("path") or "").strip()
            if not raw_path:
                continue
            source_type = str(item.get("source_type") or item.get("type") or "").strip() or None
            normalized.append(BackupSourceInput(local_path=raw_path, source_type=source_type))
            continue
        raw_path = str(item).strip()
        if raw_path:
            normalized.append(BackupSourceInput(local_path=raw_path, source_type=None))
    return normalized


def _bool_option(options: dict[str, Any], name: str, default: bool) -> bool:
    value = options.get(name)
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


def _int_option(options: dict[str, Any], name: str, default: int, *, minimum: int = 1) -> int:
    value = options.get(name)
    if value is None or value == "":
        return default
    parsed = int(value)
    if parsed < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return parsed


@dataclass(slots=True)
class OperationHandle:
    operation_id: str
    kind: str
    status: str = "pending"
    stage: str = "queued"
    message: str = "已排队"
    progress: float = 0.0
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)
    started_at: str | None = None
    finished_at: str | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    cancel_requested: bool = False

    def mark_running(self, message: str = "正在执行") -> None:
        self.status = "running"
        self.stage = "running"
        self.message = message
        self.progress = max(self.progress, 0.05)
        self.started_at = self.started_at or _utc_now()
        self.updated_at = _utc_now()

    def update(self, *, stage: str | None = None, message: str | None = None, progress: float | None = None) -> None:
        if stage is not None:
            self.stage = stage
        if message is not None:
            self.message = message
        if progress is not None:
            self.progress = max(0.0, min(1.0, progress))
        self.updated_at = _utc_now()

    def mark_complete(self, result: dict[str, Any]) -> None:
        self.status = "completed"
        self.stage = "completed"
        self.message = "操作完成"
        self.progress = 1.0
        self.result = result
        self.finished_at = _utc_now()
        self.updated_at = self.finished_at

    def mark_failed(self, exc: BaseException) -> None:
        self.status = "failed"
        self.stage = "failed"
        self.message = "操作失败"
        self.progress = max(self.progress, 0.0)
        self.error = _operation_error(exc)
        self.finished_at = _utc_now()
        self.updated_at = self.finished_at

    def cancel(self) -> None:
        self.cancel_requested = True
        if self.status in {"pending", "running"}:
            self.status = "canceling"
            self.message = "正在请求取消"
            self.updated_at = _utc_now()

    def to_dto(self) -> dict[str, Any]:
        dto = {
            "operation_id": self.operation_id,
            "kind": self.kind,
            "status": self.status,
            "stage": self.stage,
            "message": self.message,
            "progress": self.progress,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "cancel_requested": self.cancel_requested,
        }
        if self.result is not None:
            dto["result"] = self.result
        if self.error is not None:
            dto["error"] = {k: v for k, v in self.error.items() if k != "trace"}
        return dto


class OperationRegistry:
    def __init__(self, *, inline: bool = False) -> None:
        self._inline = inline
        self._lock = threading.RLock()
        self._operations: dict[str, OperationHandle] = {}
        self._executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="auto-backup-webview")

    def submit(self, kind: str, func: OperationCallable) -> OperationHandle:
        operation = OperationHandle(operation_id=secrets.token_hex(12), kind=kind)
        with self._lock:
            self._operations[operation.operation_id] = operation

        def runner() -> None:
            try:
                operation.mark_running()
                result = func(operation)
                operation.mark_complete(result)
            except BaseException as exc:  # noqa: BLE001 - expose failure as operation status.
                operation.mark_failed(exc)

        if self._inline:
            runner()
        else:
            self._executor.submit(runner)
        return operation

    def get(self, operation_id: str) -> OperationHandle | None:
        with self._lock:
            return self._operations.get(operation_id)

    def cancel(self, operation_id: str) -> OperationHandle | None:
        operation = self.get(operation_id)
        if operation is not None:
            operation.cancel()
        return operation

    def list_recent(self, *, limit: int = 8) -> list[OperationHandle]:
        with self._lock:
            operations = sorted(self._operations.values(), key=lambda item: item.created_at, reverse=True)
        return operations[:limit]


class AutoBackupWebviewBridge:
    """Thread-safe pywebview bridge for the static desktop UI.

    pywebview invokes exposed methods on worker threads. All mutating operations
    are funneled through ``_write_lock`` so backup, restore, cleanup, and repair
    jobs cannot trample the same SQLite/task state concurrently.
    """

    def __init__(
        self,
        *,
        settings: ClientSettings | None = None,
        store: SQLiteClientStore | None = None,
        device_id: str | None = None,
        run_operations_inline: bool = False,
        backup_runner: OperationCallable | None = None,
        device_credentials_resolver: DeviceCredentialResolver | None = None,
        auto_resolve_device_credentials: bool = True,
    ) -> None:
        self.settings = settings or ClientSettings.from_env()
        Path(self.settings.local_data_dir).mkdir(parents=True, exist_ok=True)
        Path(self.settings.local_cache_dir).mkdir(parents=True, exist_ok=True)
        self.store = store or SQLiteClientStore(self.settings.local_sqlite_path)
        self.store.migrate()
        self.device_credential_source = "未加载"
        self.device_credential_error = ""
        resolved_device_id = device_id or ""
        if auto_resolve_device_credentials:
            resolver = device_credentials_resolver or resolve_or_register_device_credentials
            try:
                credentials, source = resolver(
                    cloud_api_base_url=self.settings.cloud_api_base_url,
                    provided_device_token=self.settings.device_token,
                )
                device_token = str(getattr(credentials, "device_token", "") or "")
                if device_token and device_token != self.settings.device_token:
                    self.settings = replace(self.settings, device_token=device_token)
                resolved_device_id = resolved_device_id or str(getattr(credentials, "device_id", "") or "")
                self.device_credential_source = source
            except BaseException as exc:  # noqa: BLE001 - UI must still open if credential recovery fails.
                self.device_credential_source = "加载失败"
                self.device_credential_error = _safe_error(exc)["message"]
        else:
            self.device_credential_source = "已跳过"
        self._device_id_resolved = bool(resolved_device_id)
        self.device_id = resolved_device_id or "device-unresolved"
        self._write_lock = threading.RLock()
        self._window: Any | None = None
        self._operations = OperationRegistry(inline=run_operations_inline)
        self._backup_runner = backup_runner
        self._last_reconcile_report: Any | None = None
        self._auth_session_id: str = ""
        self._pending_sources: dict[str, BackupSourceInput] = {}
        self._kdf_store = PasswordKDFStore.from_env()

    def set_window(self, window: Any) -> None:
        self._window = window

    def get_app_state(self) -> dict[str, Any]:
        return self._guard(self._get_app_state)

    def list_jobs(self) -> dict[str, Any]:
        return self._guard(lambda: {"jobs": self._list_jobs_dto(limit=100)})

    def create_job(self, name: str, sources: list[Any]) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            source_inputs = self._source_inputs_from_api(sources)
            if not source_inputs:
                raise ValueError("请至少选择一个备份来源")
            manager = BackupJobManager(self.store, device_id=self.device_id)
            job = manager.create_job(sources=source_inputs, job_name=name)
            return {"job": self._job_to_dto(job)}

        return self._guard_write(work)

    def start_job(self, job_id: str, passwords: dict[str, Any], options: dict[str, Any] | None = None) -> dict[str, Any]:
        def work(operation: OperationHandle) -> dict[str, Any]:
            if self._backup_runner is not None:
                return self._backup_runner(operation)
            return self._run_backup_job(operation, job_id=job_id, passwords=passwords or {}, options=options or {})

        operation = self._operations.submit("backup", lambda op: self._serialized_operation(work, op))
        return self._ok({"operation": operation.to_dto()})

    def transition_job(self, job_id: str, action: str) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            action_to_status = {
                "pause": "paused",
                "cancel": "canceled",
                "resume": "running",
            }
            next_status = action_to_status.get(action)
            if next_status is None:
                raise ValueError(f"不支持的任务动作：{action}")
            manager = BackupJobManager(self.store, device_id=self.device_id)
            job = manager.transition_job(job_id, next_status)
            return {"job": self._job_to_dto(job)}

        return self._guard_write(work)

    def choose_sources(self, kind: str = "file") -> dict[str, Any]:
        return self._guard(lambda: {"sources": self._choose_sources(kind=kind)})

    def choose_directory(self, purpose: str = "general") -> dict[str, Any]:
        return self._guard(lambda: {"directory": self._choose_directory(purpose=purpose)})

    def list_baidu_accounts(self) -> dict[str, Any]:
        return self._guard(self._list_baidu_accounts)

    def start_baidu_authorization(self) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            state = self._with_auth_workflow(lambda workflow: workflow.start_device_code_session())
            self._auth_session_id = state.session.session_id
            return {"authorization": self._auth_state_to_dto(state)}

        return self._guard_write(work)

    def poll_baidu_authorization(self) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            if not self._auth_session_id:
                raise ValueError("请先开始百度授权")
            state = self._with_auth_workflow(lambda workflow: workflow.poll_session(self._auth_session_id))
            return {"authorization": self._auth_state_to_dto(state)}

        return self._guard_write(work)

    def complete_baidu_authorization(self, password: str) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            if not self._auth_session_id:
                raise ValueError("请先开始百度授权")
            completion = self._with_auth_workflow(
                lambda workflow: workflow.complete_password_session(
                    self._auth_session_id,
                    authorization_password=password,
                )
            )
            self._auth_session_id = ""
            return {"account": self._baidu_account_to_dto(completion.result.account)}

        return self._guard_write(work)

    def select_baidu_account(self, account_id: str) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            account = self._with_auth_workflow(lambda workflow: workflow.select_account(account_id))
            return {"account": self._baidu_account_to_dto(account)}

        return self._guard_write(work)

    def verify_baidu_token(self, account_id: str, password: str) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            try:
                decrypted = self._with_auth_workflow(
                    lambda workflow: workflow.decrypt_password_token(
                        account_id=account_id,
                        authorization_password=password,
                    )
                )
                return {
                    "verification": {
                        "account_id": account_id,
                        "valid": True,
                        "message": "百度 token 可解密",
                        "token_version": decrypted.encrypted.token_version,
                        "token_expires_at": decrypted.encrypted.token_expires_at.isoformat(),
                    }
                }
            except BaseException as exc:  # noqa: BLE001 - token self-test must stay in safe DTO shape.
                error = _safe_error(exc)
                return {
                    "verification": {
                        "account_id": account_id,
                        "valid": False,
                        "message": error["message"],
                    }
                }

        return self._guard(work)

    def list_source_mappings(self, filter: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: A002 - API name.
        return self._guard(lambda: self._list_source_mappings(filter or {}))

    def run_remote_reconcile(self, scope: dict[str, Any]) -> dict[str, Any]:
        operation = self._operations.submit(
            "remote_reconcile",
            lambda op: self._serialized_operation(lambda inner: self._run_remote_reconcile(inner, scope), op),
        )
        return self._ok({"operation": operation.to_dto()})

    def apply_remote_repairs(self, selection: dict[str, Any] | list[Any], confirmation: str) -> dict[str, Any]:
        operation = self._operations.submit(
            "remote_repair",
            lambda op: self._serialized_operation(lambda inner: self._apply_remote_repairs(inner, selection, confirmation), op),
        )
        return self._ok({"operation": operation.to_dto()})

    def list_cleanup_candidates(self, filter: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: A002 - API name.
        return self._guard(lambda: self._list_cleanup_candidates(filter or {}))

    def apply_cleanup(self, selection: list[str], options: dict[str, Any]) -> dict[str, Any]:
        operation = self._operations.submit(
            "cleanup",
            lambda op: self._serialized_operation(lambda inner: self._apply_cleanup(inner, selection, options or {}), op),
        )
        return self._ok({"operation": operation.to_dto()})

    def list_restore_candidates(self, filter: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: A002 - API name.
        return self._guard(lambda: self._list_restore_candidates(filter or {}))

    def apply_restore(self, selection: list[str], options: dict[str, Any]) -> dict[str, Any]:
        operation = self._operations.submit(
            "restore",
            lambda op: self._serialized_operation(lambda inner: self._apply_restore(inner, selection, options or {}), op),
        )
        return self._ok({"operation": operation.to_dto()})

    def get_operation(self, operation_id: str) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            operation = self._operations.get(operation_id)
            if operation is None:
                raise ValueError("操作不存在或已过期")
            return {"operation": operation.to_dto()}

        return self._guard(work)

    def cancel_operation(self, operation_id: str) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            operation = self._operations.cancel(operation_id)
            if operation is None:
                raise ValueError("操作不存在或已过期")
            return {"operation": operation.to_dto()}

        return self._guard(work)

    def get_cloud_sync_summary(self, entity_id: str) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            with self._cloud_client() as cloud:
                summary = cloud.get_entity_summary(entity_id)
            return {"summary": self._cloud_sync_summary_to_dto(summary)}

        return self._guard(work)

    def _guard(self, func: BridgeCallable) -> dict[str, Any]:
        try:
            return self._ok(func())
        except BaseException as exc:  # noqa: BLE001 - bridge must never throw raw details into JS.
            return self._err(exc)

    def _guard_write(self, func: BridgeCallable) -> dict[str, Any]:
        def wrapped() -> dict[str, Any]:
            self._require_device_ready()
            with self._write_lock:
                return func()

        return self._guard(wrapped)

    def _serialized_operation(self, func: OperationCallable, operation: OperationHandle) -> dict[str, Any]:
        self._require_device_ready()
        with self._write_lock:
            if operation.cancel_requested:
                raise RuntimeError("操作已取消")
            return func(operation)

    def _ok(self, data: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "data": data}

    def _err(self, exc: BaseException) -> dict[str, Any]:
        return {"ok": False, "error": _safe_error(exc)}

    def _require_device_ready(self) -> None:
        if not self._device_id_resolved:
            raise RuntimeError("设备凭据未就绪，无法确认本机真实 Device ID")

    def _get_app_state(self) -> dict[str, Any]:
        jobs = self._list_jobs_dto(limit=8)
        status_counts: dict[str, int] = {}
        for job in jobs:
            status_counts[job["status"]] = status_counts.get(job["status"], 0) + 1
        risks: list[dict[str, str]] = []
        if not self.settings.device_token:
            risks.append({"level": "warning", "title": "设备未注册", "message": "云端授权和同步需要先恢复或注册 Device Token"})
        if not self._device_id_resolved:
            risks.append({"level": "danger", "title": "设备 ID 未确认", "message": "写入类操作已暂停，避免用临时设备 ID 污染备份记录"})
        if self.device_credential_error:
            risks.append({"level": "warning", "title": "设备凭据加载失败", "message": self.device_credential_error})
        if not any(job["status"] == "completed" for job in jobs):
            risks.append({"level": "info", "title": "尚无完成备份", "message": "创建并完成首个备份后可进入恢复演练"})
        if any(job["status"] == "failed" for job in jobs):
            risks.append({"level": "danger", "title": "存在失败任务", "message": "建议先查看最近任务并继续或重新执行"})
        operations = [operation.to_dto() for operation in self._operations.list_recent()]
        accounts = self._try_accounts_summary()
        return {
            "app": {
                "name": "Auto Backup BD Netdisk",
                "version": "desktop",
                "device_id_hint": _device_id_hint(self.device_id),
                "cloud_api_base_url": self.settings.cloud_api_base_url,
                "device_credential_source": self.device_credential_source,
                "device_credential_error": self.device_credential_error,
                "device_token_available": bool(self.settings.device_token),
                "device_id_resolved": self._device_id_resolved,
            },
            "dashboard": {
                "jobs": jobs,
                "status_counts": status_counts,
                "risks": risks,
                "operations": operations,
                "accounts": accounts,
            },
            "settings": {
                "upload": {
                    "root_dir": DEFAULT_BACKUP_ROOT_DIR,
                    "part_size": DEFAULT_PART_SIZE,
                    "max_archive_size_bytes": DEFAULT_MAX_ARCHIVE_SIZE_BYTES,
                    "run_upload": True,
                    "check_quota": True,
                    "sync_outbox": True,
                    "reconcile_remote": True,
                    "enforce_cache_budget": False,
                    "cleanup_cache_artifacts": False,
                }
            },
        }

    def _try_accounts_summary(self) -> dict[str, Any]:
        if not self.settings.device_token:
            return {"available": False, "selected_account_id": None, "items": []}
        try:
            data = self._list_baidu_accounts()
            accounts = data["accounts"]
            return {
                "available": True,
                "selected_account_id": data.get("selected_account_id"),
                "items": accounts[:3],
            }
        except BaseException:
            return {"available": False, "selected_account_id": None, "items": []}

    def _list_jobs_dto(self, *, limit: int) -> list[dict[str, Any]]:
        manager = BackupJobManager(self.store, device_id=self.device_id)
        return [self._job_to_dto(job) for job in manager.list_jobs(limit=limit)]

    def _source_inputs_from_api(self, sources: Iterable[Any]) -> list[BackupSourceInput]:
        normalized: list[BackupSourceInput] = []
        for item in sources:
            if isinstance(item, dict):
                token = str(item.get("source_token") or "").strip()
                if token:
                    cached = self._pending_sources.get(token)
                    if cached is None:
                        raise ValueError("来源选择已过期，请重新选择")
                    normalized.append(cached)
                    continue
            normalized.extend(_as_source_inputs([item]))
        return normalized

    def _job_to_dto(self, job: Any) -> dict[str, Any]:
        job_record = getattr(job, "job", job)
        sources = [self._source_to_dto(source) for source in getattr(job, "sources", ())]
        return {
            "job_id": job_record.backup_job_id,
            "name": job_record.job_name,
            "status": job_record.status,
            "status_label": _status_label(job_record.status),
            "created_at": job_record.created_at,
            "updated_at": job_record.updated_at,
            "last_stage": job_record.last_stage,
            "sync_status": job_record.sync_status,
            "last_error": self._redact_optional(job_record.last_error),
            "sources": sources,
            "source_count": len(sources),
        }

    def _source_to_dto(self, source: Any) -> dict[str, Any]:
        return {
            "source_id": source.backup_source_id,
            "source_type": source.source_type,
            "display_name": source.display_name,
            "path_digest": _short_digest(source.path_sha256, length=16),
            "created_at": source.created_at,
        }

    def _redact_optional(self, value: str | None) -> str | None:
        if not value:
            return value
        if "\\" in value or ":/" in value:
            return "错误信息包含本地路径，已脱敏"
        return value[:300]

    def _choose_sources(self, *, kind: str) -> list[dict[str, Any]]:
        if self._window is None:
            return []
        try:
            import webview  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - exercised only without runtime dependency.
            raise RuntimeError("pywebview 未安装，无法打开原生文件选择器") from exc
        selected: list[str] = []
        if kind == "mixed":
            selected.extend(str(path) for path in (self._window.create_file_dialog(dialog_type=webview.OPEN_DIALOG, allow_multiple=True) or []))
            selected.extend(str(path) for path in (self._window.create_file_dialog(dialog_type=webview.FOLDER_DIALOG, allow_multiple=True) or []))
        else:
            dialog_type = webview.FOLDER_DIALOG if kind == "directory" else webview.OPEN_DIALOG
            selected.extend(str(path) for path in (self._window.create_file_dialog(dialog_type=dialog_type, allow_multiple=True) or []))
        if not selected:
            return []
        source_inputs = normalize_sources([BackupSourceInput(local_path=str(path), source_type=detect_source_type(str(path))) for path in selected])
        result = []
        for item in source_inputs:
            source_token = secrets.token_hex(12)
            self._pending_sources[source_token] = item
            result.append(
                {
                    "source_token": source_token,
                    "source_type": item.source_type,
                    "display_name": Path(item.local_path).name or "已选择来源",
                    "path_digest": _short_digest(path_sha256(item.local_path), length=16),
                }
            )
        return result

    def _choose_directory(self, *, purpose: str) -> str | None:
        if self._window is None:
            return None
        try:
            import webview  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("pywebview 未安装，无法打开原生目录选择器") from exc
        selected = self._window.create_file_dialog(dialog_type=webview.FOLDER_DIALOG, allow_multiple=False)
        if not selected:
            return None
        return str(selected[0])

    def _auth_workflow(self) -> BaiduAuthWorkflow:
        return BaiduAuthWorkflow(cloud_client=self._cloud_client(), kdf_store=self._kdf_store, device_id=self.device_id)

    def _with_auth_workflow(self, func: Callable[[BaiduAuthWorkflow], Any]) -> Any:
        with self._cloud_client() as cloud_client:
            return func(BaiduAuthWorkflow(cloud_client=cloud_client, kdf_store=self._kdf_store, device_id=self.device_id))

    def _cloud_client(self) -> BaiduCloudClient:
        return BaiduCloudClient(
            base_url=self.settings.cloud_api_base_url,
            device_token=self.settings.device_token,
            device_id=self.device_id,
        )

    def _cloud_sync_summary_to_dto(self, summary: Any) -> dict[str, Any]:
        return {
            "entity_id": summary.entity_id,
            "entity_type": summary.entity_type,
            "data_version": summary.data_version,
            "revision_id": summary.revision_id,
            "revision_id_hint": _edge_hint(summary.revision_id),
            "canonical_record_sha256": summary.canonical_record_sha256,
            "canonical_record_sha256_hint": _short_digest(summary.canonical_record_sha256, length=16),
            "updated_by_device_id": summary.updated_by_device_id,
            "updated_by_device_hint": _device_id_hint(summary.updated_by_device_id),
            "deleted_at": summary.deleted_at.isoformat() if summary.deleted_at else None,
            "recent_revisions": [
                {
                    "event_id": revision.event_id,
                    "event_id_hint": _edge_hint(revision.event_id),
                    "revision_id": revision.revision_id,
                    "revision_id_hint": _edge_hint(revision.revision_id),
                    "data_version": revision.data_version,
                    "apply_status": revision.apply_status,
                    "canonical_record_sha256": revision.canonical_record_sha256,
                    "canonical_record_sha256_hint": _short_digest(revision.canonical_record_sha256, length=16),
                    "created_at": revision.created_at.isoformat(),
                }
                for revision in summary.recent_revisions
            ],
        }

    def _list_baidu_accounts(self) -> dict[str, Any]:
        accounts = self._with_auth_workflow(lambda workflow: workflow.load_accounts())
        selected = next((account.account_id for account in accounts if account.selected), "")
        return {
            "selected_account_id": selected,
            "accounts": [self._baidu_account_to_dto(account) for account in accounts],
        }

    def _baidu_account_to_dto(self, account: Any) -> dict[str, Any]:
        baidu_uk = str(getattr(account, "baidu_uk", "") or "")
        baidu_uid = str(getattr(account, "baidu_uid", "") or "")
        device_id = str(getattr(account, "device_id", "") or "")
        account_id = str(account.account_id)
        has_local_kdf = self._has_local_kdf_record(account_id)
        return {
            "account_id": account_id,
            "baidu_uk": _edge_hint(baidu_uk),
            "uid_hint": _edge_hint(baidu_uid or baidu_uk),
            "device_hint": _device_id_hint(device_id),
            "current_device": bool(getattr(account, "current_device", False)),
            "display_name": account.display_name,
            "selected": bool(getattr(account, "selected", False)),
            "token_valid": bool(account.token_valid),
            "token_expires_at": account.token_expires_at.isoformat(),
            "last_verify_status": account.last_verify_status,
            "local_kdf_available": has_local_kdf,
        }

    def _auth_state_to_dto(self, state: Any) -> dict[str, Any]:
        session = state.session
        return {
            "session_id": session.session_id,
            "status": session.status,
            "can_complete": state.can_complete,
            "terminal": state.terminal,
            "user_action_url": state.user_action_url,
            "user_code": session.user_code,
            "qrcode_url": session.qrcode_url,
            "expires_at": session.expires_at.isoformat(),
            "error_code": session.error_code,
        }

    def _selected_account_id(self, options: dict[str, Any]) -> str:
        account_id = str(options.get("account_id") or "").strip()
        if account_id:
            return account_id
        accounts = self._with_auth_workflow(lambda workflow: workflow.load_accounts())
        selected = next((account for account in accounts if account.selected), None)
        if selected is None:
            raise ValueError("请先选择百度账号")
        return selected.account_id

    def _baidu_client_from_password(self, account_id: str, password: str) -> BaiduNetdiskClient:
        if not password:
            raise ValueError("需要输入一次性授权密码才能解密百度 token")
        token = self._with_auth_workflow(
            lambda workflow: workflow.decrypt_password_token(account_id=account_id, authorization_password=password)
        )
        return BaiduNetdiskClient(access_token=token.access_token)

    def _has_local_kdf_record(self, account_id: str) -> bool:
        try:
            return self._kdf_store.get_record(account_id, device_id=self.device_id) is not None
        except BaseException:
            return False

    def _refresh_history(self) -> None:
        if not self.settings.device_token:
            return
        try:
            refresher = DeviceBackupHistoryRefresher(
                store=self.store,
                cloud_api_base_url=self.settings.cloud_api_base_url,
                device_token=self.settings.device_token,
                device_id=self.device_id,
            )
            refresher.refresh()
        except BaseException:
            return

    def _pipeline_options_from_api(self, *, account_id: str, archive_password: str, options: dict[str, Any]) -> BackupPipelineOptions:
        root_dir = str(options.get("root_dir") or DEFAULT_BACKUP_ROOT_DIR).strip()
        if not root_dir:
            raise ValueError("百度远端根目录不能为空")
        return BackupPipelineOptions(
            cache_root=self.settings.local_cache_dir,
            password=archive_password,
            account_id=account_id,
            root_dir=root_dir,
            run_upload=_bool_option(options, "run_upload", True),
            check_quota=_bool_option(options, "check_quota", True),
            sync_outbox=_bool_option(options, "sync_outbox", True),
            reconcile_remote=_bool_option(options, "reconcile_remote", True),
            enforce_cache_budget=_bool_option(options, "enforce_cache_budget", False),
            max_archive_size_bytes=_int_option(
                options,
                "max_archive_size_bytes",
                DEFAULT_MAX_ARCHIVE_SIZE_BYTES,
                minimum=64 * 1024**2,
            ),
            part_size=_int_option(options, "part_size", DEFAULT_PART_SIZE, minimum=1024 * 1024),
            cleanup_cache_artifacts=_bool_option(options, "cleanup_cache_artifacts", False),
        )

    def _run_backup_job(self, operation: OperationHandle, *, job_id: str, passwords: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
        operation.update(stage="preflight", message="正在准备备份参数", progress=0.1)
        archive_password = str(passwords.get("archive_password") or "")
        auth_password = str(passwords.get("authorization_password") or "")
        if not archive_password:
            raise ValueError("需要输入备份压缩密码")
        account_id = self._selected_account_id(options)
        job_state = BackupJobManager(self.store, device_id=self.device_id).get_job_with_sources(job_id).job
        if job_state.status == "paused":
            BackupJobManager(self.store, device_id=self.device_id).transition_job(job_id, "running")
        operation.update(stage="authorization", message="正在解密百度授权", progress=0.18)
        with self._baidu_client_from_password(account_id, auth_password) as baidu_client, self._cloud_client() as cloud_client:
            pipeline = BackupPipeline(
                store=self.store,
                device_id=self.device_id,
                baidu_client=baidu_client,
                cloud_client=cloud_client,
            )
            pipeline_options = self._pipeline_options_from_api(account_id=account_id, archive_password=archive_password, options=options)
            operation.update(stage="backup", message="正在执行扫描、归档、上传与同步", progress=0.32)
            summary = pipeline.run_job(job_id, pipeline_options)
        operation.update(stage="finalize", message="正在整理任务摘要", progress=0.92)
        return self._pipeline_summary_to_dto(summary)

    def _pipeline_summary_to_dto(self, summary: Any) -> dict[str, Any]:
        return {
            "job_id": summary.backup_job_id,
            "final_stage": summary.final_stage,
            "completed": summary.completed,
            "archive_id": summary.archive.archive_id,
            "archive_seq": summary.archive.archive_seq,
            "files_scanned": summary.scan.file_count,
            "folders_scanned": summary.scan.folder_count,
            "archive_size": summary.archive.archive_size,
            "archive_size_label": _size_label(summary.archive.archive_size),
            "upload_status": "completed" if summary.upload else "skipped",
            "remote_dir_hint": _short_digest(path_sha256(summary.upload.remote_archive_path), length=16) if summary.upload else None,
            "sync_summary": summary.sync.__dict__ if summary.sync else None,
            "reconcile_summary": {
                "status_counts": summary.reconcile.status_counts,
                "has_differences": summary.reconcile.has_differences,
            }
            if summary.reconcile
            else None,
        }

    def _list_source_mappings(self, filter_data: dict[str, Any]) -> dict[str, Any]:
        self._refresh_history()
        query = SourceMappingQuery(self.store)
        report = query.list_rows(
            backup_job_id=str(filter_data.get("job_id") or ""),
            keyword=str(filter_data.get("keyword") or ""),
            limit=int(filter_data.get("limit") or 200),
        )
        return {
            "summary": report.summary.__dict__,
            "rows": [
                {
                    "job_id": row.backup_job_id,
                    "job_name": row.job_name,
                    "job_status": row.job_status,
                    "source_type": row.source_type,
                    "source_display_name": row.source_display_name,
                    "path_digest": _short_digest(row.source_path_sha256, length=16),
                    "row_id": row.file_item_id,
                    "display_name": row.display_name,
                    "relative_path_digest": _short_digest(row.relative_path_sha256, length=16),
                    "archive_id": row.archive_id,
                    "baidu_ready": row.baidu_ready,
                    "remote_archive_status": row.remote_archive_status,
                    "remote_meta_status": row.remote_meta_status,
                    "job_index_status": row.job_index_status,
                    "cleanup_status": row.cleanup_status,
                    "restore_status": row.restore_status,
                }
                for row in report.rows
            ],
        }

    def _list_cleanup_candidates(self, filter_data: dict[str, Any]) -> dict[str, Any]:
        self._refresh_history()
        service = SourceCleanupService(self.store, device_id=self.device_id)
        report = service.list_candidates(
            backup_job_id=str(filter_data.get("job_id") or ""),
            keyword=str(filter_data.get("keyword") or ""),
            limit=int(filter_data.get("limit") or 200),
        )
        return {
            "summary": {
                "total_count": len(report.candidates),
                "eligible_count": report.eligible_count,
                "blocked_count": report.blocked_count,
                "sync_pending_count": report.sync_pending_count,
            },
            "confirm_text": CLEANUP_CONFIRM_TEXT,
            "permanent_confirm_text": PERMANENT_DELETE_CONFIRM_TEXT,
            "candidates": [
                {
                    "content_reference_id": item.content_reference_id,
                    "job_id": item.backup_job_id,
                    "job_name": item.job_name,
                    "source_display_name": item.display_name,
                    "path_digest": _short_digest(item.path_sha256, length=16),
                    "archive_id": item.archive_id,
                    "archive_status": item.archive_verify_status,
                    "cleanup_status": item.cleanup_status,
                    "ready": item.eligible,
                    "blockers": [] if item.eligible else [item.reason],
                    "size_label": _size_label(item.size_bytes),
                    "sync_pending_warning": item.sync_pending_warning,
                }
                for item in report.candidates
            ],
        }

    def _apply_cleanup(self, operation: OperationHandle, selection: list[str], options: dict[str, Any]) -> dict[str, Any]:
        operation.update(stage="preflight", message="正在校验清理确认词", progress=0.12)
        if not selection:
            raise ValueError("请先选择需要清理的候选")
        method = str(options.get("method") or "recycle_bin")
        if method == "permanent_delete" and not bool(options.get("advanced_enabled", False)):
            raise ValueError("永久删除需要先启用高级清理选项")
        service = SourceCleanupService(self.store, device_id=self.device_id)
        result = service.apply(
            content_reference_ids=tuple(selection),
            method=method,
            dry_run=bool(options.get("dry_run", True)),
            quarantine_dir=options.get("quarantine_dir") or None,
            confirm_text=str(options.get("confirm_text") or ""),
            permanent_confirm_text=str(options.get("permanent_confirm_text") or ""),
        )
        return {
            "dry_run": bool(options.get("dry_run", True)),
            "method": result.method,
            "selected_count": result.requested_count,
            "applied_count": result.applied_count,
            "failed_count": result.failed_count,
            "confirm_text": CLEANUP_CONFIRM_TEXT,
            "permanent_confirm_text": PERMANENT_DELETE_CONFIRM_TEXT,
            "record_ids": result.record_ids,
        }

    def _list_restore_candidates(self, filter_data: dict[str, Any]) -> dict[str, Any]:
        self._refresh_history()
        service = RestoreService(self.store, device_id=self.device_id, cache_root=self.settings.local_cache_dir)
        report = service.list_candidates(
            backup_job_id=str(filter_data.get("job_id") or ""),
            keyword=str(filter_data.get("keyword") or ""),
            limit=int(filter_data.get("limit") or 200),
        )
        return {
            "summary": {
                "total_count": len(report.candidates),
                "restorable_count": report.restorable_count,
                "local_ready_count": report.local_ready_count,
                "needs_download_count": report.needs_download_count,
                "blocked_count": report.blocked_count,
            },
            "candidates": [
                {
                    "restore_candidate_id": item.restore_candidate_id,
                    "job_id": item.backup_job_id,
                    "job_name": item.job_name,
                    "backup_source_id": item.backup_source_id,
                    "source_type": item.source_type,
                    "source_display_name": item.source_display_name,
                    "path_digest": _short_digest(item.source_path_sha256, length=16),
                    "archive_id": item.archive_id,
                    "archive_seq": item.archive_seq,
                    "archive_status": item.archive_verify_status,
                    "remote_archive_status": item.remote_archive_status,
                    "cleanup_status": item.cleanup_status,
                    "ready": item.restorable,
                    "blockers": [] if item.restorable else [item.reason],
                    "size_label": _size_label(item.size_bytes),
                    "candidate_status": item.candidate_status,
                }
                for item in report.candidates
            ],
        }

    def _apply_restore(self, operation: OperationHandle, selection: list[str], options: dict[str, Any]) -> dict[str, Any]:
        operation.update(stage="preflight", message="正在准备恢复参数", progress=0.12)
        if not selection:
            raise ValueError("请先选择需要恢复的候选")
        account_id = str(options.get("account_id") or "").strip()
        auth_password = str(options.get("authorization_password") or "")
        downloader = None
        baidu_client = None
        if account_id and auth_password:
            baidu_client = self._baidu_client_from_password(account_id, auth_password)
            downloader = BaiduArchiveDownloader(baidu_client)
        try:
            service = RestoreService(
                self.store,
                device_id=self.device_id,
                cache_root=self.settings.local_cache_dir,
                downloader=downloader,
            )
            result = service.restore(
                content_reference_ids=tuple(selection),
                password=str(options.get("archive_password") or ""),
                target_mode=self._restore_target_mode(options),
                target_root=options.get("target_root") or None,
                conflict_strategy=str(options.get("conflict_strategy") or options.get("conflict_policy") or "keep_both"),
            )
        finally:
            if baidu_client is not None:
                baidu_client.close()
        return {
            "selected_count": result.requested_count,
            "restored_files": result.restored_count,
            "skipped_count": result.skipped_count,
            "failed_count": result.failed_count,
            "items": [
                {
                    "content_reference_id": item.content_reference_id,
                    "status": item.status,
                    "message": self._redact_optional(item.error_message),
                    "target_path_digest": item.target_path_sha256,
                    "final_path_digest": item.final_path_sha256,
                    "archive_source": item.archive_source,
                }
                for item in result.results
            ],
        }

    def _restore_target_mode(self, options: dict[str, Any]) -> str:
        target_mode = str(options.get("target_mode") or "manual_root")
        aliases = {
            "manual_root": "manual_path",
            "manual_path": "manual_path",
            "original_path": "original_path",
        }
        return aliases.get(target_mode, target_mode)

    def _run_remote_reconcile(self, operation: OperationHandle, scope_data: dict[str, Any]) -> dict[str, Any]:
        operation.update(stage="authorization", message="正在准备远端校对", progress=0.12)
        account_id = self._selected_account_id(scope_data)
        password = str(scope_data.get("authorization_password") or "")
        scope = RemoteReconcileScope(
            job_id=str(scope_data.get("job_id") or ""),
            upload_session_id=str(scope_data.get("upload_session_id") or ""),
            remote_dir=str(scope_data.get("remote_dir") or ""),
        )
        with self._baidu_client_from_password(account_id, password) as baidu_client:
            reconciler = RemoteObjectReconciler(store=self.store, baidu=baidu_client)
            operation.update(stage="reconcile", message="正在比对本地索引与百度远端对象", progress=0.35)
            report = reconciler.reconcile(scope)
        self._last_reconcile_report = report
        return self._reconcile_report_to_dto(report)

    def _apply_remote_repairs(self, operation: OperationHandle, selection: dict[str, Any] | list[Any], confirmation: str) -> dict[str, Any]:
        if confirmation != CONFIRM_REPAIR_TEXT:
            raise ValueError(f"远端修复确认词必须为 {CONFIRM_REPAIR_TEXT}")
        if self._last_reconcile_report is None:
            raise ValueError("请先运行一次远端校对")
        action_filter = None
        dry_run = True
        if isinstance(selection, dict):
            raw_filter = selection.get("action_filter")
            if isinstance(raw_filter, list):
                action_filter = [str(item) for item in raw_filter]
            dry_run = bool(selection.get("dry_run", True))
        plan = build_remote_repair_plan(self._last_reconcile_report, action_filter=action_filter)
        operation.update(stage="repair", message="正在应用远端索引修复", progress=0.32)
        repairer = RemoteObjectRepairer(store=self.store, updated_by_device_id=self.device_id)
        result = repairer.apply(plan, dry_run=dry_run)
        return {
            "confirm_text": CONFIRM_REPAIR_TEXT,
            "dry_run": result.dry_run,
            "candidate_count": result.candidate_count,
            "writable_count": result.writable_count,
            "selected_count": result.selected_count,
            "applied_count": result.applied_count,
            "items": [
                {
                    "remote_object_id": item.local_remote_object_id,
                    "revision_id": item.revision_id,
                    "data_version": item.data_version,
                    "action": item.action,
                }
                for item in result.applied_records
            ],
        }

    def _reconcile_report_to_dto(self, report: Any) -> dict[str, Any]:
        return {
            "summary": {
                "scope_type": report.scope.scope_type,
                "scope_value_digest": _short_digest(path_sha256(report.scope.scope_value), length=16),
                "local_object_count": report.local_object_count,
                "remote_object_count": report.remote_object_count,
                "status_counts": report.status_counts,
                "has_differences": report.has_differences,
            },
            "confirm_text": CONFIRM_REPAIR_TEXT,
            "items": [
                {
                    "remote_object_id": item.local_remote_object_id,
                    "archive_id": item.archive_id,
                    "status": item.status,
                    "expected_size": item.local_size,
                    "observed_size": item.remote_size,
                    "remote_path_digest": _short_digest(path_sha256(item.remote_path), length=16) if item.remote_path else None,
                    "message": self._redact_optional(item.suggestion),
                }
                for item in report.findings
            ],
        }


__all__ = ["AutoBackupWebviewBridge", "OperationHandle", "OperationRegistry"]
