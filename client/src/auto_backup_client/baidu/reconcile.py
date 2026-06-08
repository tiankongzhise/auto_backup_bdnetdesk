from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Mapping

from auto_backup_client.baidu.upload import BaiduFileItem, BaiduNetdiskClient, BaiduNetdiskError, normalize_baidu_path
from auto_backup_client.sqlite_store import SQLiteClientStore


STATUS_CONSISTENT = "consistent"
STATUS_DB_EXISTS_REMOTE_MISSING = "db_exists_remote_missing"
STATUS_REMOTE_META_MISSING = "remote_meta_missing"
STATUS_REMOTE_META_MISMATCH = "remote_meta_mismatch"
STATUS_REMOTE_SIZE_MISMATCH = "remote_size_mismatch"
STATUS_FS_ID_CHANGED = "fs_id_changed"
STATUS_BAIDU_ONLY = "baidu_only"
STATUS_REMOTE_UNREADABLE = "remote_unreadable"

RECONCILE_STATUSES = frozenset(
    {
        STATUS_CONSISTENT,
        STATUS_DB_EXISTS_REMOTE_MISSING,
        STATUS_REMOTE_META_MISSING,
        STATUS_REMOTE_META_MISMATCH,
        STATUS_REMOTE_SIZE_MISMATCH,
        STATUS_FS_ID_CHANGED,
        STATUS_BAIDU_ONLY,
        STATUS_REMOTE_UNREADABLE,
    }
)


@dataclass(frozen=True)
class RemoteReconcileScope:
    job_id: str = ""
    upload_session_id: str = ""
    remote_dir: str = ""
    recursive: bool = True
    page_limit: int = 1000

    def __post_init__(self) -> None:
        if sum(bool(value.strip()) for value in (self.job_id, self.upload_session_id, self.remote_dir)) != 1:
            raise ValueError("exactly one of job_id, upload_session_id, or remote_dir is required")
        if self.page_limit < 1:
            raise ValueError("page_limit must be >= 1")

    @property
    def scope_type(self) -> str:
        if self.job_id.strip():
            return "job_id"
        if self.upload_session_id.strip():
            return "upload_session_id"
        return "remote_dir"

    @property
    def scope_value(self) -> str:
        if self.job_id.strip():
            return self.job_id.strip()
        if self.upload_session_id.strip():
            return self.upload_session_id.strip()
        return normalize_baidu_path(self.remote_dir, require_backup_root=False)


@dataclass(frozen=True)
class RemoteReconcileFinding:
    status: str
    object_type: str
    remote_path: str
    suggestion: str
    job_id: str = ""
    archive_id: str = ""
    archive_sha256: str = ""
    local_remote_object_id: str = ""
    local_size: int | None = None
    remote_size: int | None = None
    local_md5: str = ""
    remote_md5: str = ""
    local_fs_id: int | None = None
    remote_fs_id: int | None = None
    error_code: str = ""


@dataclass(frozen=True)
class RemoteReconcileReport:
    scope: RemoteReconcileScope
    local_object_count: int
    remote_object_count: int
    findings: tuple[RemoteReconcileFinding, ...] = field(default_factory=tuple)
    unreadable_dirs: tuple[str, ...] = tuple()

    @property
    def status_counts(self) -> dict[str, int]:
        counts = {status: 0 for status in sorted(RECONCILE_STATUSES)}
        for finding in self.findings:
            counts[finding.status] = counts.get(finding.status, 0) + 1
        return counts

    @property
    def has_differences(self) -> bool:
        return any(finding.status != STATUS_CONSISTENT for finding in self.findings)


class RequestRateLimiter:
    def __init__(
        self,
        *,
        max_requests_per_minute: int = 8,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_requests_per_minute < 1:
            raise ValueError("max_requests_per_minute must be >= 1")
        self.min_interval_seconds = 60.0 / float(max_requests_per_minute)
        self._sleeper = sleeper
        self._request_count = 0

    def before_request(self) -> None:
        if self._request_count > 0:
            self._sleeper(self.min_interval_seconds)
        self._request_count += 1


class RemoteObjectReconciler:
    def __init__(
        self,
        *,
        store: SQLiteClientStore,
        baidu: BaiduNetdiskClient,
        rate_limiter: RequestRateLimiter | None = None,
    ) -> None:
        self._store = store
        self._baidu = baidu
        self._rate_limiter = rate_limiter or RequestRateLimiter()

    def reconcile(self, scope: RemoteReconcileScope) -> RemoteReconcileReport:
        local_objects = self._store.list_remote_objects_for_reconcile(
            job_id=scope.job_id,
            upload_session_id=scope.upload_session_id,
            remote_dir=scope.remote_dir,
        )
        upload_sessions = self._store.list_upload_sessions_for_reconcile(
            job_id=scope.job_id,
            upload_session_id=scope.upload_session_id,
            remote_dir=scope.remote_dir,
        )
        remote_dirs = _remote_dirs_for_scope(scope, local_objects, upload_sessions)
        remote_items, unreadable = self._read_remote_items(remote_dirs, scope=scope)
        findings = _build_findings(local_objects, upload_sessions, remote_items, unreadable_dirs=unreadable)
        return RemoteReconcileReport(
            scope=scope,
            local_object_count=len(local_objects),
            remote_object_count=len(remote_items),
            findings=tuple(findings),
            unreadable_dirs=tuple(unreadable),
        )

    def _read_remote_items(
        self,
        remote_dirs: tuple[str, ...],
        *,
        scope: RemoteReconcileScope,
    ) -> tuple[dict[str, BaiduFileItem], list[str]]:
        remote_items: dict[str, BaiduFileItem] = {}
        unreadable: list[str] = []
        for remote_dir in remote_dirs:
            try:
                if scope.recursive:
                    for item in self._iter_list_all(remote_dir, page_limit=scope.page_limit):
                        if not item.isdir:
                            remote_items[item.path] = item
                else:
                    self._rate_limiter.before_request()
                    page = self._baidu.list_dir(remote_dir=remote_dir, limit=scope.page_limit)
                    for item in page.items:
                        if not item.isdir:
                            remote_items[item.path] = item
            except BaiduNetdiskError:
                unreadable.append(remote_dir)
        return remote_items, unreadable

    def _iter_list_all(self, remote_dir: str, *, page_limit: int) -> tuple[BaiduFileItem, ...]:
        items: list[BaiduFileItem] = []
        start = 0
        while True:
            self._rate_limiter.before_request()
            page = self._baidu.list_all(remote_path=remote_dir, start=start, limit=page_limit, recursion=True)
            items.extend(page.items)
            if not page.has_more:
                break
            start = page.cursor or (start + len(page.items))
            if not page.items:
                break
        return tuple(items)


def _remote_dirs_for_scope(
    scope: RemoteReconcileScope,
    local_objects: list[dict[str, object]],
    upload_sessions: list[dict[str, object]],
) -> tuple[str, ...]:
    if scope.remote_dir.strip():
        return (normalize_baidu_path(scope.remote_dir, require_backup_root=False),)
    dirs: set[str] = set()
    for row in local_objects:
        remote_path = str(row.get("remote_path", "")).strip()
        if remote_path:
            dirs.add(_job_dir_from_remote_path(remote_path))
    for session in upload_sessions:
        for key in ("remote_archive_path", "remote_meta_path", "remote_job_index_path"):
            remote_path = str(session.get(key, "")).strip()
            if remote_path:
                dirs.add(_job_dir_from_remote_path(remote_path))
    return tuple(sorted(dirs))


def _build_findings(
    local_objects: list[dict[str, object]],
    upload_sessions: list[dict[str, object]],
    remote_items: Mapping[str, BaiduFileItem],
    *,
    unreadable_dirs: list[str],
) -> list[RemoteReconcileFinding]:
    findings: list[RemoteReconcileFinding] = []
    local_by_path = {str(row["remote_path"]): row for row in local_objects if str(row.get("remote_path", "")).strip()}
    unreadable_set = set(unreadable_dirs)
    reported_paths: set[str] = set()

    for row in local_objects:
        remote_path = str(row.get("remote_path", "")).strip()
        if not remote_path:
            continue
        unreadable_dir = _matching_unreadable_dir(remote_path, unreadable_set)
        if unreadable_dir:
            findings.append(_finding(row=row, status=STATUS_REMOTE_UNREADABLE, remote_path=remote_path, error_code="remote_unreadable"))
            reported_paths.add(remote_path)
            continue
        remote_item = remote_items.get(remote_path)
        if remote_item is None:
            status = (
                STATUS_REMOTE_META_MISSING
                if str(row.get("object_type", "")) in {"archive_meta", "job_index"}
                else STATUS_DB_EXISTS_REMOTE_MISSING
            )
            findings.append(_finding(row=row, status=status, remote_path=remote_path))
            reported_paths.add(remote_path)
            continue
        findings.append(_compare_row_to_item(row, remote_item))
        reported_paths.add(remote_path)

    for session in upload_sessions:
        for missing_path, missing_object_type in (
            (str(session.get("remote_archive_path", "")).strip(), "archive"),
            (str(session.get("remote_meta_path", "")).strip(), "archive_meta"),
            (str(session.get("remote_job_index_path", "")).strip(), "job_index"),
        ):
            if not missing_path or missing_path in remote_items or missing_path in reported_paths:
                continue
            unreadable_dir = _matching_unreadable_dir(missing_path, unreadable_set)
            if unreadable_dir:
                status = STATUS_REMOTE_UNREADABLE
            elif missing_object_type == "archive":
                status = STATUS_DB_EXISTS_REMOTE_MISSING
            else:
                status = STATUS_REMOTE_META_MISSING
            findings.append(
                RemoteReconcileFinding(
                    status=status,
                    object_type=missing_object_type,
                    remote_path=missing_path,
                    suggestion=_suggestion_for_status(status),
                    job_id=str(session.get("job_id", "")),
                    archive_id=str(session.get("archive_id", "")),
                    archive_sha256=str(session.get("archive_sha256", "")),
                    local_size=None,
                    remote_size=None,
                    error_code="remote_unreadable" if unreadable_dir else "",
                )
            )
            reported_paths.add(missing_path)

    for remote_path, item in sorted(remote_items.items()):
        if remote_path not in local_by_path:
            findings.append(
                RemoteReconcileFinding(
                    status=STATUS_BAIDU_ONLY,
                    object_type=_infer_object_type(remote_path),
                    remote_path=remote_path,
                    suggestion=_suggestion_for_status(STATUS_BAIDU_ONLY),
                    remote_size=item.size,
                    remote_md5=item.md5,
                    remote_fs_id=item.fs_id,
                )
            )

    if not local_objects and not remote_items:
        for remote_dir in unreadable_dirs:
            findings.append(
                RemoteReconcileFinding(
                    status=STATUS_REMOTE_UNREADABLE,
                    object_type="directory",
                    remote_path=remote_dir,
                    suggestion=_suggestion_for_status(STATUS_REMOTE_UNREADABLE),
                    error_code="remote_unreadable",
                )
            )
    return findings


def _compare_row_to_item(row: Mapping[str, object], item: BaiduFileItem) -> RemoteReconcileFinding:
    object_type = str(row.get("object_type", ""))
    local_size = int(row.get("size_bytes", 0) or 0)
    local_md5 = str(row.get("md5", ""))
    local_fs_id = _optional_int(row.get("fs_id"))
    if local_size != item.size:
        status = STATUS_REMOTE_META_MISMATCH if object_type in {"archive_meta", "job_index"} else STATUS_REMOTE_SIZE_MISMATCH
    elif local_md5 and item.md5 and local_md5.lower() != item.md5.lower():
        status = STATUS_REMOTE_META_MISMATCH
    elif local_fs_id not in (None, 0) and item.fs_id not in (0, local_fs_id):
        status = STATUS_FS_ID_CHANGED
    else:
        status = STATUS_CONSISTENT
    return _finding(
        row=row,
        status=status,
        remote_path=item.path,
        remote_size=item.size,
        remote_md5=item.md5,
        remote_fs_id=item.fs_id,
    )


def _finding(
    *,
    row: Mapping[str, object],
    status: str,
    remote_path: str,
    remote_size: int | None = None,
    remote_md5: str = "",
    remote_fs_id: int | None = None,
    error_code: str = "",
) -> RemoteReconcileFinding:
    return RemoteReconcileFinding(
        status=status,
        object_type=str(row.get("object_type", "")),
        remote_path=remote_path,
        suggestion=_suggestion_for_status(status),
        job_id=str(row.get("job_id", "")),
        archive_id=str(row.get("archive_id", "")),
        archive_sha256=str(row.get("archive_sha256", "")),
        local_remote_object_id=str(row.get("remote_object_id", "")),
        local_size=int(row.get("size_bytes", 0) or 0),
        remote_size=remote_size,
        local_md5=str(row.get("md5", "")),
        remote_md5=remote_md5,
        local_fs_id=_optional_int(row.get("fs_id")),
        remote_fs_id=remote_fs_id,
        error_code=error_code,
    )


def _suggestion_for_status(status: str) -> str:
    return {
        STATUS_CONSISTENT: "no_action",
        STATUS_DB_EXISTS_REMOTE_MISSING: "mark_remote_missing_or_reupload",
        STATUS_REMOTE_META_MISSING: "reupload_meta_or_rebuild_metadata",
        STATUS_REMOTE_META_MISMATCH: "inspect_meta_before_repair",
        STATUS_REMOTE_SIZE_MISMATCH: "keep_difference_and_reupload_if_needed",
        STATUS_FS_ID_CHANGED: "verify_path_then_update_fs_id",
        STATUS_BAIDU_ONLY: "rebuild_db_from_baidu_or_keep_difference",
        STATUS_REMOTE_UNREADABLE: "retry_after_baidu_access_check",
    }[status]


def _job_dir_from_remote_path(remote_path: str) -> str:
    cleaned = normalize_baidu_path(remote_path, require_backup_root=False)
    if "/archives/" in cleaned:
        return cleaned.split("/archives/", 1)[0]
    return cleaned.rsplit("/", 1)[0]


def _matching_unreadable_dir(remote_path: str, unreadable_dirs: set[str]) -> str:
    for remote_dir in unreadable_dirs:
        if remote_path == remote_dir or remote_path.startswith(remote_dir.rstrip("/") + "/"):
            return remote_dir
    return ""


def _infer_object_type(remote_path: str) -> str:
    if remote_path.endswith(".meta.json"):
        return "archive_meta"
    if remote_path.endswith("/job.index.json") or remote_path.endswith("job.index.json"):
        return "job_index"
    if remote_path.endswith(".7z"):
        return "archive"
    return "unknown"


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(value)
