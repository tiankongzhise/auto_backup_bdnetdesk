from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Protocol

from auto_backup_client.backup_jobs import path_sha256
from auto_backup_client.file_identity import FileIdentity, identity_matches, read_file_identity
from auto_backup_client.local_fs import make_dirs, native_path
from auto_backup_client.sqlite_store import SQLiteClientStore, build_version_fields, new_id, utc_now_iso


CLEANUP_CONFIRM_TEXT = "CLEANUP_SOURCES"
PERMANENT_DELETE_CONFIRM_TEXT = "PERMANENT_DELETE_SOURCES"

CleanupMethod = Literal["recycle_bin", "quarantine", "permanent_delete"]
CleanupStatus = Literal[
    "eligible",
    "already_cleaned",
    "not_completed",
    "not_packaged",
    "not_verified",
    "not_uploaded",
    "remote_not_confirmed",
    "source_missing",
    "source_changed",
]


class CleanupFileOperator(Protocol):
    def move_to_recycle_bin(self, path: Path) -> None:
        ...

    def move_to_quarantine(self, path: Path, target: Path) -> None:
        ...

    def permanent_delete(self, path: Path) -> None:
        ...


@dataclass(frozen=True)
class CleanupCandidate:
    content_reference_id: str
    file_item_id: str
    backup_job_id: str
    job_name: str
    job_status: str
    job_sync_status: str
    device_id: str
    display_name: str
    local_path: str
    path_sha256: str
    size_bytes: int
    sha256: str
    mtime_ns: int
    file_volume_serial: str
    file_index: str
    archive_id: str
    archive_verify_status: str
    upload_status: str
    meta_status: str
    job_index_status: str
    remote_archive_status: str
    remote_meta_status: str
    remote_job_index_status: str
    cleanup_status: str
    candidate_status: CleanupStatus
    reason: str
    sync_pending_warning: bool

    @property
    def eligible(self) -> bool:
        return self.candidate_status == "eligible"


@dataclass(frozen=True)
class CleanupReport:
    candidates: tuple[CleanupCandidate, ...]
    eligible_count: int
    blocked_count: int
    sync_pending_count: int


@dataclass(frozen=True)
class CleanupApplyResult:
    requested_count: int
    applied_count: int
    failed_count: int
    method: CleanupMethod
    record_ids: tuple[str, ...]


class SourceCleanupError(ValueError):
    pass


class SourceCleanupService:
    def __init__(
        self,
        store: SQLiteClientStore,
        *,
        device_id: str,
        operator: CleanupFileOperator | None = None,
        identity_reader: Callable[[str | Path], FileIdentity] | None = None,
    ) -> None:
        cleaned_device_id = device_id.strip()
        if not cleaned_device_id:
            raise SourceCleanupError("device_id is required")
        self.store = store
        self.device_id = cleaned_device_id
        self.operator = operator or WindowsFileCleanupOperator()
        self.identity_reader = identity_reader or read_file_identity

    def list_candidates(self, *, backup_job_id: str = "", keyword: str = "", limit: int = 500) -> CleanupReport:
        if limit < 1 or limit > 5000:
            raise SourceCleanupError("cleanup candidate limit must be between 1 and 5000")
        cleaned_job_id = backup_job_id.strip()
        cleaned_keyword = " ".join(keyword.strip().split()).casefold()
        rows = self._candidate_rows(backup_job_id=cleaned_job_id, keyword=cleaned_keyword, limit=limit)
        candidates = tuple(_candidate_from_row(row) for row in rows)
        return CleanupReport(
            candidates=candidates,
            eligible_count=sum(1 for candidate in candidates if candidate.eligible),
            blocked_count=sum(1 for candidate in candidates if not candidate.eligible),
            sync_pending_count=sum(1 for candidate in candidates if candidate.sync_pending_warning),
        )

    def apply(
        self,
        *,
        backup_job_id: str = "",
        content_reference_ids: tuple[str, ...] = (),
        method: CleanupMethod = "recycle_bin",
        quarantine_dir: str | Path | None = None,
        cleanup_operator: str = "local-user",
        confirm_text: str = "",
        permanent_confirm_text: str = "",
        now: str | None = None,
        dry_run: bool = True,
    ) -> CleanupApplyResult:
        cleaned_operator = " ".join(cleanup_operator.strip().split()) or "local-user"
        cleaned_method = _validate_method(method)
        if not dry_run and confirm_text.strip() != CLEANUP_CONFIRM_TEXT:
            raise SourceCleanupError("cleanup confirmation phrase is required")
        if not dry_run and cleaned_method == "permanent_delete" and permanent_confirm_text.strip() != PERMANENT_DELETE_CONFIRM_TEXT:
            raise SourceCleanupError("permanent delete confirmation phrase is required")
        selected_ids = tuple(dict.fromkeys(ref.strip() for ref in content_reference_ids if ref.strip()))
        report = self.list_candidates(backup_job_id=backup_job_id, limit=5000)
        candidates = tuple(candidate for candidate in report.candidates if not selected_ids or candidate.content_reference_id in selected_ids)
        if selected_ids and len(candidates) != len(selected_ids):
            raise SourceCleanupError("one or more selected cleanup references were not found")
        eligible = tuple(candidate for candidate in candidates if candidate.eligible)
        if len(eligible) != len(candidates):
            raise SourceCleanupError("all selected cleanup candidates must be eligible")
        if cleaned_method == "quarantine" and quarantine_dir is None:
            raise SourceCleanupError("quarantine_dir is required for quarantine cleanup")

        record_ids: list[str] = []
        failed_count = 0
        actual_now = now or utc_now_iso()
        for candidate in eligible:
            observed, mismatch_reason = self._verify_candidate(candidate)
            if dry_run:
                continue
            if mismatch_reason:
                record_ids.append(
                    self._write_record(
                        candidate,
                        method=cleaned_method,
                        status="failed",
                        cleanup_operator=cleaned_operator,
                        observed=observed,
                        quarantine_path="",
                        error_code="source_changed",
                        error_message=mismatch_reason,
                        now=actual_now,
                    )
                )
                failed_count += 1
                continue
            quarantine_target = ""
            try:
                if cleaned_method == "recycle_bin":
                    self.operator.move_to_recycle_bin(Path(candidate.local_path))
                    status = "moved_to_recycle_bin"
                    reference_status = "cleaned"
                elif cleaned_method == "quarantine":
                    target = _quarantine_target(Path(quarantine_dir or ""), candidate)
                    self.operator.move_to_quarantine(Path(candidate.local_path), target)
                    quarantine_target = str(target)
                    status = "moved_to_quarantine"
                    reference_status = "cleaned"
                else:
                    self.operator.permanent_delete(Path(candidate.local_path))
                    status = "permanently_deleted"
                    reference_status = "cleaned"
            except Exception as exc:
                record_ids.append(
                    self._write_record(
                        candidate,
                        method=cleaned_method,
                        status="failed",
                        cleanup_operator=cleaned_operator,
                        observed=observed,
                        quarantine_path=quarantine_target,
                        error_code="cleanup_failed",
                        error_message=_safe_error(exc),
                        now=actual_now,
                    )
                )
                failed_count += 1
                continue
            record_ids.append(
                self._write_record(
                    candidate,
                    method=cleaned_method,
                    status=status,
                    cleanup_operator=cleaned_operator,
                    observed=observed,
                    quarantine_path=quarantine_target,
                    error_code="",
                    error_message="",
                    now=actual_now,
                    reference_cleanup_status=reference_status,
                )
            )

        return CleanupApplyResult(
            requested_count=len(candidates),
            applied_count=0 if dry_run else len(candidates) - failed_count,
            failed_count=0 if dry_run else failed_count,
            method=cleaned_method,
            record_ids=tuple(record_ids),
        )

    def _candidate_rows(self, *, backup_job_id: str, keyword: str, limit: int) -> list[dict[str, object]]:
        params: list[object] = []
        where: list[str] = []
        if backup_job_id:
            where.append("j.backup_job_id = ?")
            params.append(backup_job_id)
        if keyword:
            like = f"%{keyword}%"
            where.append(
                """
                (
                    lower(j.job_name) LIKE ?
                    OR lower(cr.display_name) LIKE ?
                    OR lower(cr.relative_path) LIKE ?
                    OR lower(cr.content_id) LIKE ?
                    OR lower(cr.file_sha256) LIKE ?
                )
                """
            )
            params.extend([like, like, like, like, like])
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        with self.store.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    j.backup_job_id,
                    j.job_name,
                    j.status AS job_status,
                    j.sync_status AS job_sync_status,
                    j.device_id,
                    cr.content_reference_id,
                    cr.file_item_id,
                    cr.local_path,
                    cr.path_sha256,
                    cr.display_name,
                    cr.size_bytes,
                    cr.file_sha256,
                    cr.cleanup_status,
                    f.mtime_ns,
                    f.file_volume_serial,
                    f.file_index,
                    cr.archive_id,
                    a.verify_status AS archive_verify_status,
                    us.upload_status,
                    us.meta_status,
                    us.job_index_status,
                    ro_archive.status AS remote_archive_status,
                    ro_meta.status AS remote_meta_status,
                    ro_job.status AS remote_job_index_status
                FROM content_references cr
                JOIN backup_jobs j ON j.backup_job_id = cr.backup_job_id
                JOIN file_items f ON f.file_item_id = cr.file_item_id
                LEFT JOIN archives a ON a.archive_id = cr.archive_id
                LEFT JOIN upload_sessions us ON us.archive_id = cr.archive_id AND us.job_id = cr.backup_job_id
                LEFT JOIN remote_objects ro_archive
                    ON ro_archive.archive_id = cr.archive_id
                   AND ro_archive.object_type = 'archive'
                LEFT JOIN remote_objects ro_meta
                    ON ro_meta.archive_id = cr.archive_id
                   AND ro_meta.object_type = 'archive_meta'
                LEFT JOIN remote_objects ro_job
                    ON ro_job.job_id = cr.backup_job_id
                   AND ro_job.object_type = 'job_index'
                {where_sql}
                ORDER BY j.created_at DESC, cr.source_seq, cr.relative_path, cr.content_reference_id
                LIMIT ?
                """,
                tuple(params + [limit]),
            ).fetchall()
            return [dict(row) for row in rows]

    def _verify_candidate(self, candidate: CleanupCandidate) -> tuple[FileIdentity | None, str]:
        try:
            observed = self.identity_reader(candidate.local_path)
        except FileNotFoundError:
            return None, "source file is missing"
        except OSError as exc:
            return None, _safe_error(exc)
        if not identity_matches(
            observed,
            expected_size=candidate.size_bytes,
            expected_mtime_ns=candidate.mtime_ns,
            expected_volume_serial=candidate.file_volume_serial,
            expected_file_index=candidate.file_index,
        ):
            return observed, "source file identity changed since backup"
        return observed, ""

    def _write_record(
        self,
        candidate: CleanupCandidate,
        *,
        method: CleanupMethod,
        status: str,
        cleanup_operator: str,
        observed: FileIdentity | None,
        quarantine_path: str,
        error_code: str,
        error_message: str,
        now: str,
        reference_cleanup_status: str = "cleanup_failed",
    ) -> str:
        record_id = new_id("cleanup")
        observed_size = observed.size_bytes if observed is not None else None
        observed_mtime = observed.mtime_ns if observed is not None else None
        payload = build_version_fields(
            entity_payload={
                "source_cleanup_record_id": record_id,
                "entity_id": f"source_cleanup_record_{record_id}",
                "backup_job_id": candidate.backup_job_id,
                "content_reference_id": candidate.content_reference_id,
                "file_item_id": candidate.file_item_id,
                "device_id": candidate.device_id,
                "original_path": candidate.local_path,
                "original_path_sha256": candidate.path_sha256,
                "display_name": candidate.display_name,
                "cleanup_status": status,
                "cleanup_method": method,
                "cleanup_operator": cleanup_operator,
                "cleanup_time": now if status != "requested" else None,
                "pre_cleanup_size": candidate.size_bytes,
                "pre_cleanup_sha256": candidate.sha256,
                "pre_cleanup_mtime_ns": candidate.mtime_ns,
                "pre_cleanup_volume_serial": candidate.file_volume_serial,
                "pre_cleanup_file_index": candidate.file_index,
                "observed_size": observed_size,
                "observed_mtime_ns": observed_mtime,
                "observed_volume_serial": observed.volume_serial if observed is not None else "",
                "observed_file_index": observed.file_index if observed is not None else "",
                "quarantine_path": quarantine_path,
                "quarantine_path_sha256": path_sha256(quarantine_path) if quarantine_path else "",
                "error_code": error_code,
                "error_message": error_message,
                "created_at": now,
            },
            updated_by_device_id=self.device_id,
            now=now,
            sync_status="sync_pending",
        )
        with self.store.transaction() as conn:
            self.store.put_source_cleanup_record(conn, payload)
            self.store.update_content_reference_cleanup_status(
                conn,
                content_reference_id=candidate.content_reference_id,
                cleanup_status=reference_cleanup_status,
                updated_at=now,
                updated_by_device_id=self.device_id,
            )
        return record_id


class WindowsFileCleanupOperator:
    def move_to_recycle_bin(self, path: Path) -> None:
        if os.name != "nt":
            raise SourceCleanupError("recycle bin cleanup is only available on Windows")
        _move_to_windows_recycle_bin(path)

    def move_to_quarantine(self, path: Path, target: Path) -> None:
        make_dirs(target.parent)
        shutil.move(native_path(path), native_path(target))

    def permanent_delete(self, path: Path) -> None:
        os.remove(native_path(path))


def _candidate_from_row(row: dict[str, object]) -> CleanupCandidate:
    status, reason = _candidate_status(row)
    sync_pending_warning = str(row["job_sync_status"]) != "synced"
    return CleanupCandidate(
        content_reference_id=str(row["content_reference_id"]),
        file_item_id=str(row["file_item_id"]),
        backup_job_id=str(row["backup_job_id"]),
        job_name=str(row["job_name"]),
        job_status=str(row["job_status"]),
        job_sync_status=str(row["job_sync_status"]),
        device_id=str(row["device_id"]),
        display_name=str(row["display_name"]),
        local_path=str(row["local_path"]),
        path_sha256=str(row["path_sha256"]),
        size_bytes=int(row["size_bytes"] or 0),
        sha256=str(row["file_sha256"]),
        mtime_ns=int(row["mtime_ns"] or 0),
        file_volume_serial=str(row["file_volume_serial"] or ""),
        file_index=str(row["file_index"] or ""),
        archive_id=str(row["archive_id"] or ""),
        archive_verify_status=str(row["archive_verify_status"] or ""),
        upload_status=str(row["upload_status"] or ""),
        meta_status=str(row["meta_status"] or ""),
        job_index_status=str(row["job_index_status"] or ""),
        remote_archive_status=str(row["remote_archive_status"] or ""),
        remote_meta_status=str(row["remote_meta_status"] or ""),
        remote_job_index_status=str(row["remote_job_index_status"] or ""),
        cleanup_status=str(row["cleanup_status"] or "not_cleaned"),
        candidate_status=status,
        reason=reason,
        sync_pending_warning=sync_pending_warning,
    )


def _candidate_status(row: dict[str, object]) -> tuple[CleanupStatus, str]:
    if str(row["cleanup_status"]) == "cleaned":
        return "already_cleaned", "source was already cleaned"
    if str(row["job_status"]) != "completed":
        return "not_completed", "backup job is not completed"
    if not str(row["archive_id"] or ""):
        return "not_packaged", "source has no archive assignment"
    if str(row["archive_verify_status"] or "") != "standard_test_passed":
        return "not_verified", "archive standard verification is not passed"
    if str(row["upload_status"] or "") != "remote_created":
        return "not_uploaded", "archive upload is not remote_created"
    if str(row["meta_status"] or "") != "uploaded" or str(row["job_index_status"] or "") != "uploaded":
        return "not_uploaded", "archive metadata or job index is not uploaded"
    if (
        str(row["remote_archive_status"] or "") != "remote_created"
        or str(row["remote_meta_status"] or "") != "remote_created"
        or str(row["remote_job_index_status"] or "") != "remote_created"
    ):
        return "remote_not_confirmed", "remote objects are not all confirmed"
    local_path = str(row["local_path"])
    try:
        observed = read_file_identity(local_path)
    except FileNotFoundError:
        return "source_missing", "source file is missing"
    except OSError as exc:
        return "source_missing", _safe_error(exc)
    expected_volume = str(row["file_volume_serial"] or "")
    expected_index = str(row["file_index"] or "")
    if not identity_matches(
        observed,
        expected_size=int(row["size_bytes"] or 0),
        expected_mtime_ns=int(row["mtime_ns"] or 0),
        expected_volume_serial=expected_volume,
        expected_file_index=expected_index,
    ):
        return "source_changed", "source file changed since backup"
    return "eligible", "ready for manual cleanup"


def _validate_method(method: str) -> CleanupMethod:
    if method not in {"recycle_bin", "quarantine", "permanent_delete"}:
        raise SourceCleanupError("unsupported cleanup method")
    return method  # type: ignore[return-value]


def _quarantine_target(quarantine_dir: Path, candidate: CleanupCandidate) -> Path:
    if not str(quarantine_dir).strip():
        raise SourceCleanupError("quarantine_dir is required")
    suffix = Path(candidate.local_path).suffix
    name = f"{candidate.backup_job_id}-{candidate.content_reference_id}-{candidate.path_sha256[:12]}{suffix}"
    return quarantine_dir / candidate.backup_job_id / name


def _safe_error(exc: Exception) -> str:
    text = str(exc)
    if len(text) > 300:
        text = text[:297] + "..."
    return text.replace("\n", " ").replace("\r", " ")


def _move_to_windows_recycle_bin(path: Path) -> None:
    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", ctypes.wintypes.HWND),
            ("wFunc", ctypes.wintypes.UINT),
            ("pFrom", ctypes.wintypes.LPCWSTR),
            ("pTo", ctypes.wintypes.LPCWSTR),
            ("fFlags", ctypes.wintypes.USHORT),
            ("fAnyOperationsAborted", ctypes.wintypes.BOOL),
            ("hNameMappings", ctypes.wintypes.LPVOID),
            ("lpszProgressTitle", ctypes.wintypes.LPCWSTR),
        ]

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    sh_file_operation = shell32.SHFileOperationW
    sh_file_operation.argtypes = [ctypes.POINTER(SHFILEOPSTRUCTW)]
    sh_file_operation.restype = ctypes.c_int

    FO_DELETE = 0x0003
    FOF_ALLOWUNDO = 0x0040
    FOF_NOCONFIRMATION = 0x0010
    FOF_NOERRORUI = 0x0400
    FOF_SILENT = 0x0004

    errors: list[Exception] = []
    variants = tuple(dict.fromkeys((str(path.resolve()), native_path(path))))
    for variant in variants:
        source = variant + "\0\0"
        operation = SHFILEOPSTRUCTW(
            hwnd=None,
            wFunc=FO_DELETE,
            pFrom=source,
            pTo=None,
            fFlags=FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT,
            fAnyOperationsAborted=False,
            hNameMappings=None,
            lpszProgressTitle=None,
        )
        result = sh_file_operation(ctypes.byref(operation))
        if result == 0 and not operation.fAnyOperationsAborted:
            return
        if operation.fAnyOperationsAborted:
            errors.append(SourceCleanupError("recycle-bin operation was aborted"))
        else:
            errors.append(OSError(result, "SHFileOperationW recycle-bin delete failed", str(path)))
    raise errors[-1]
