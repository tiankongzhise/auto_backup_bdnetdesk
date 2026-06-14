from __future__ import annotations

import hashlib
import os
import stat
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Literal, Sequence

from auto_backup_client.backup_jobs import BackupJobError, BackupJobWithSources, path_sha256
from auto_backup_client.file_identity import read_file_identity
from auto_backup_client.sqlite_store import SQLiteClientStore, build_version_fields, new_id, stable_json_dumps, utc_now_iso


MiB = 1024 * 1024
QUICK_FULL_READ_LIMIT = 16 * MiB
QUICK_SAMPLE_SIZE = MiB
QUICK_OFFSET_ALIGNMENT = 4096
FILE_ATTRIBUTE_REPARSE_POINT = 0x400

ScanIssueType = Literal[
    "missing_source",
    "skipped_symlink",
    "skipped_junction",
    "skipped_shortcut",
    "unreadable_file",
    "unreadable_directory",
    "unsupported_source",
]


@dataclass(frozen=True)
class SampleRange:
    offset: int
    size: int


@dataclass(frozen=True)
class FileFingerprint:
    size_bytes: int
    quick_fingerprint: str
    quick_sample_count: int
    quick_sample_size: int
    sample_plan_json: str
    md5: str
    sha256: str
    content_id: str
    scan_status: str
    stat_after: os.stat_result


@dataclass(frozen=True)
class ScanIssue:
    local_path: str
    relative_path: str
    display_name: str
    issue_type: ScanIssueType
    error_message: str = ""


@dataclass
class _FileDraft:
    file_item_id: str
    backup_source_id: str
    source_seq: int
    local_path: str
    relative_path: str
    display_name: str
    stat_result: os.stat_result
    fingerprint: FileFingerprint
    parent_folder_item_id: str = ""


@dataclass
class _FolderDraft:
    folder_item_id: str
    backup_source_id: str
    source_seq: int
    local_path: str
    relative_path: str
    display_name: str
    stat_result: os.stat_result
    parent_folder_item_id: str = ""
    files: list[_FileDraft] = field(default_factory=list)
    folders: list["_FolderDraft"] = field(default_factory=list)
    direct_issue_count: int = 0
    folder_content_hash: str = ""
    folder_manifest_hash: str = ""
    child_file_count: int = 0
    child_folder_count: int = 0
    total_file_count: int = 0
    total_folder_count: int = 0
    total_issue_count: int = 0


@dataclass(frozen=True)
class SourceScanResult:
    backup_source_id: str
    file_count: int
    folder_count: int
    issue_count: int


@dataclass(frozen=True)
class JobScanResult:
    backup_job_id: str
    file_count: int
    folder_count: int
    issue_count: int
    sources: tuple[SourceScanResult, ...]


class BackupScanError(ValueError):
    pass


class BackupScanner:
    def __init__(
        self,
        store: SQLiteClientStore,
        *,
        device_id: str,
        fingerprint_file_func: Callable[[Path], FileFingerprint] | None = None,
    ) -> None:
        cleaned_device_id = device_id.strip()
        if not cleaned_device_id:
            raise BackupScanError("device_id is required")
        self.store = store
        self.device_id = cleaned_device_id
        self._fingerprint_file = fingerprint_file_func or fingerprint_file

    def scan_job(self, backup_job_id: str, *, now: str | None = None) -> JobScanResult:
        cleaned_job_id = backup_job_id.strip()
        if not cleaned_job_id:
            raise BackupScanError("backup_job_id is required")
        job_with_sources = self._load_job_with_sources(cleaned_job_id)
        actual_now = now or utc_now_iso()
        source_results: list[SourceScanResult] = []
        source_drafts: list[tuple[str, list[_FileDraft], list[_FolderDraft], list[ScanIssue]]] = []

        for source in job_with_sources.sources:
            files: list[_FileDraft] = []
            folders: list[_FolderDraft] = []
            issues: list[ScanIssue] = []
            source_path = Path(source.local_path)
            if source.source_type == "file":
                file_draft = self._scan_file_source(source_path, source.backup_source_id, source.source_seq, issues)
                if file_draft is not None:
                    files.append(file_draft)
            elif source.source_type == "directory":
                folder_draft = self._scan_directory_source(source_path, source.backup_source_id, source.source_seq, issues)
                if folder_draft is not None:
                    _finalize_folder_hashes(folder_draft)
                    folders.extend(_flatten_folders(folder_draft))
                    files.extend(_flatten_files(folder_draft))
            else:
                issues.append(_issue(source_path, "", "unsupported_source", "unsupported backup source type"))

            source_results.append(
                SourceScanResult(
                    backup_source_id=source.backup_source_id,
                    file_count=len(files),
                    folder_count=len(folders),
                    issue_count=len(issues),
                )
            )
            source_drafts.append((source.backup_source_id, files, folders, issues))

        with self.store.transaction() as conn:
            for backup_source_id, files, folders, issues in source_drafts:
                next_versions = self.store.replace_scan_results_for_source(
                    conn,
                    backup_job_id=cleaned_job_id,
                    backup_source_id=backup_source_id,
                )
                for folder in folders:
                    self.store.put_folder_item(
                        conn,
                        _folder_payload(
                            folder,
                            backup_job_id=cleaned_job_id,
                            device_id=self.device_id,
                            now=actual_now,
                            data_version=next_versions.get(folder.folder_item_id, 1),
                        ),
                    )
                for file_draft in files:
                    self.store.put_file_item(
                        conn,
                        _file_payload(
                            file_draft,
                            backup_job_id=cleaned_job_id,
                            device_id=self.device_id,
                            now=actual_now,
                            data_version=next_versions.get(file_draft.file_item_id, 1),
                        ),
                    )
                for issue in issues:
                    self.store.put_scan_issue(
                        conn,
                        _issue_payload(
                            issue,
                            backup_job_id=cleaned_job_id,
                            backup_source_id=backup_source_id,
                            now=actual_now,
                        ),
                    )

        return JobScanResult(
            backup_job_id=cleaned_job_id,
            file_count=sum(source.file_count for source in source_results),
            folder_count=sum(source.folder_count for source in source_results),
            issue_count=sum(source.issue_count for source in source_results),
            sources=tuple(source_results),
        )

    def mark_file_changed(
        self,
        *,
        backup_job_id: str,
        file_item_id: str,
        now: str | None = None,
    ) -> None:
        cleaned_job_id = backup_job_id.strip()
        cleaned_file_item_id = file_item_id.strip()
        if not cleaned_job_id or not cleaned_file_item_id:
            raise BackupScanError("backup_job_id and file_item_id are required")
        actual_now = now or utc_now_iso()
        with self.store.transaction() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM file_items
                WHERE backup_job_id = ? AND file_item_id = ?
                """,
                (cleaned_job_id, cleaned_file_item_id),
            ).fetchone()
            if row is None:
                raise BackupScanError("file item not found")
            payload = dict(row)
            payload["scan_status"] = "changed_during_scan"
            payload["updated_at"] = actual_now
            versioned = build_version_fields(
                entity_payload=payload,
                updated_by_device_id=self.device_id,
                data_version=int(row["data_version"]) + 1,
                schema_version=int(row["schema_version"]),
                now=actual_now,
                sync_status="sync_pending",
                deleted_at=row["deleted_at"],
                last_synced_revision_id=row["last_synced_revision_id"],
            )
            self.store.put_file_item(conn, versioned)

    def _load_job_with_sources(self, backup_job_id: str) -> BackupJobWithSources:
        try:
            from auto_backup_client.backup_jobs import BackupJobManager

            return BackupJobManager(self.store, device_id=self.device_id).get_job_with_sources(backup_job_id)
        except BackupJobError as exc:
            raise BackupScanError(str(exc)) from exc

    def _scan_file_source(
        self,
        path: Path,
        backup_source_id: str,
        source_seq: int,
        issues: list[ScanIssue],
    ) -> _FileDraft | None:
        relative_path = _normalize_relative_path(path.name)
        return self._scan_file(path, backup_source_id, source_seq, relative_path, "", issues)

    def _scan_directory_source(
        self,
        path: Path,
        backup_source_id: str,
        source_seq: int,
        issues: list[ScanIssue],
    ) -> _FolderDraft | None:
        return self._scan_directory(path, backup_source_id, source_seq, "", "", issues)

    def _scan_directory(
        self,
        path: Path,
        backup_source_id: str,
        source_seq: int,
        relative_path: str,
        parent_folder_item_id: str,
        issues: list[ScanIssue],
    ) -> _FolderDraft | None:
        stat_result = _safe_lstat(path)
        if stat_result is None:
            issues.append(_issue(path, relative_path, "missing_source", "source path is missing"))
            return None
        skipped = skip_issue_type_from_metadata(
            suffix=path.suffix,
            is_symlink=path.is_symlink(),
            file_attrs=_file_attrs(stat_result),
        )
        if skipped is not None:
            issues.append(_issue(path, relative_path, skipped))
            return None
        if not stat.S_ISDIR(stat_result.st_mode):
            issues.append(_issue(path, relative_path, "unsupported_source", "source is not a directory"))
            return None

        folder = _FolderDraft(
            folder_item_id=_stable_item_id("folder", backup_source_id, relative_path),
            backup_source_id=backup_source_id,
            source_seq=source_seq,
            local_path=str(path),
            relative_path=_normalize_relative_path(relative_path),
            display_name=path.name or str(path),
            stat_result=stat_result,
            parent_folder_item_id=parent_folder_item_id,
        )
        try:
            children = sorted(path.iterdir(), key=lambda child: child.name.casefold())
        except OSError as exc:
            folder.direct_issue_count += 1
            issues.append(_issue(path, relative_path, "unreadable_directory", str(exc)))
            return folder

        for child in children:
            child_relative_path = _join_relative(relative_path, child.name)
            child_stat = _safe_lstat(child)
            if child_stat is None:
                folder.direct_issue_count += 1
                issues.append(_issue(child, child_relative_path, "missing_source", "path disappeared during scan"))
                continue
            skipped_child = skip_issue_type_from_metadata(
                suffix=child.suffix,
                is_symlink=child.is_symlink(),
                file_attrs=_file_attrs(child_stat),
            )
            if skipped_child is not None:
                folder.direct_issue_count += 1
                issues.append(_issue(child, child_relative_path, skipped_child))
                continue
            if stat.S_ISDIR(child_stat.st_mode):
                child_folder = self._scan_directory(
                    child,
                    backup_source_id,
                    source_seq,
                    child_relative_path,
                    folder.folder_item_id,
                    issues,
                )
                if child_folder is not None:
                    folder.folders.append(child_folder)
                continue
            if stat.S_ISREG(child_stat.st_mode):
                file_draft = self._scan_file(
                    child,
                    backup_source_id,
                    source_seq,
                    child_relative_path,
                    folder.folder_item_id,
                    issues,
                    stat_result=child_stat,
                )
                if file_draft is not None:
                    folder.files.append(file_draft)
                else:
                    folder.direct_issue_count += 1
                continue
            folder.direct_issue_count += 1
            issues.append(_issue(child, child_relative_path, "unsupported_source", "not a regular file or directory"))
        return folder

    def _scan_file(
        self,
        path: Path,
        backup_source_id: str,
        source_seq: int,
        relative_path: str,
        parent_folder_item_id: str,
        issues: list[ScanIssue],
        *,
        stat_result: os.stat_result | None = None,
    ) -> _FileDraft | None:
        actual_stat = stat_result or _safe_lstat(path)
        if actual_stat is None:
            issues.append(_issue(path, relative_path, "missing_source", "source path is missing"))
            return None
        skipped = skip_issue_type_from_metadata(
            suffix=path.suffix,
            is_symlink=path.is_symlink(),
            file_attrs=_file_attrs(actual_stat),
        )
        if skipped is not None:
            issues.append(_issue(path, relative_path, skipped))
            return None
        if not stat.S_ISREG(actual_stat.st_mode):
            issues.append(_issue(path, relative_path, "unsupported_source", "source is not a regular file"))
            return None
        try:
            fingerprint = self._fingerprint_file(path)
        except OSError as exc:
            issues.append(_issue(path, relative_path, "unreadable_file", str(exc)))
            return None
        return _FileDraft(
            file_item_id=_stable_item_id("file", backup_source_id, relative_path),
            backup_source_id=backup_source_id,
            source_seq=source_seq,
            local_path=str(path),
            relative_path=_normalize_relative_path(relative_path),
            display_name=path.name or str(path),
            stat_result=actual_stat,
            fingerprint=fingerprint,
            parent_folder_item_id=parent_folder_item_id,
        )


def fingerprint_file(path: str | Path) -> FileFingerprint:
    actual_path = Path(path)
    before = actual_path.stat()
    sample_hashes = []
    sample_ranges = quick_sample_ranges(before.st_size)
    with actual_path.open("rb") as handle:
        for sample_range in sample_ranges:
            handle.seek(sample_range.offset)
            sample_hashes.append(hashlib.sha256(handle.read(sample_range.size)).hexdigest())
    quick_fingerprint = hashlib.sha256(
        f"v1:qf:{before.st_size}:{len(sample_hashes)}:{':'.join(sample_hashes)}".encode("utf-8")
    ).hexdigest()

    md5_hash = hashlib.md5()
    sha256_hash = hashlib.sha256()
    with actual_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * MiB), b""):
            md5_hash.update(chunk)
            sha256_hash.update(chunk)
    after = actual_path.stat()
    sha256_value = sha256_hash.hexdigest()
    scan_status = "full_hashed"
    if before.st_size != after.st_size or _mtime_ns(before) != _mtime_ns(after):
        scan_status = "changed_during_scan"
    return FileFingerprint(
        size_bytes=after.st_size,
        quick_fingerprint=quick_fingerprint,
        quick_sample_count=len(sample_ranges),
        quick_sample_size=sum(sample_range.size for sample_range in sample_ranges),
        sample_plan_json=stable_json_dumps(
            {
                "algorithm": "v1:qf",
                "ranges": [{"offset": sample_range.offset, "size": sample_range.size} for sample_range in sample_ranges],
            }
        ),
        md5=md5_hash.hexdigest(),
        sha256=sha256_value,
        content_id=file_content_id(after.st_size, sha256_value),
        scan_status=scan_status,
        stat_after=after,
    )


def quick_sample_ranges(size_bytes: int) -> tuple[SampleRange, ...]:
    if size_bytes < 0:
        raise ValueError("file size must not be negative")
    if size_bytes <= QUICK_FULL_READ_LIMIT:
        return (SampleRange(offset=0, size=size_bytes),)

    target_count = _target_sample_count(size_bytes)
    last_required_offset = max(0, size_bytes - QUICK_SAMPLE_SIZE)
    offsets: list[int] = []
    for index in range(target_count):
        raw_offset = 0 if target_count == 1 else (last_required_offset * index) // (target_count - 1)
        aligned_offset = (raw_offset // QUICK_OFFSET_ALIGNMENT) * QUICK_OFFSET_ALIGNMENT
        if aligned_offset not in offsets:
            offsets.append(aligned_offset)

    ranges: list[SampleRange] = []
    for offset in offsets:
        sample_size = min(QUICK_SAMPLE_SIZE, size_bytes - offset)
        if offset <= last_required_offset < offset + QUICK_SAMPLE_SIZE:
            sample_size = size_bytes - offset
        ranges.append(SampleRange(offset=offset, size=sample_size))
    return tuple(ranges)


def file_content_id(size_bytes: int, file_sha256: str) -> str:
    return hashlib.sha256(f"v1:file:{size_bytes}:{file_sha256}".encode("utf-8")).hexdigest()


def skip_issue_type_from_metadata(*, suffix: str, is_symlink: bool, file_attrs: int) -> ScanIssueType | None:
    if suffix.casefold() == ".lnk":
        return "skipped_shortcut"
    if is_symlink:
        return "skipped_symlink"
    if file_attrs & FILE_ATTRIBUTE_REPARSE_POINT:
        return "skipped_junction"
    return None


def folder_content_hash(child_tokens: Iterable[tuple[str, str]]) -> str:
    counts = Counter(child_tokens)
    entries = [
        {"child_type": child_type, "child_content_id": child_content_id, "count": count}
        for (child_type, child_content_id), count in sorted(counts.items())
    ]
    return hashlib.sha256(("v1:folder-content:" + stable_json_dumps(entries)).encode("utf-8")).hexdigest()


def folder_manifest_hash(entries: Sequence[dict[str, object]]) -> str:
    sorted_entries = sorted(entries, key=lambda entry: stable_json_dumps(entry))
    return hashlib.sha256(("v1:folder-manifest:" + stable_json_dumps(sorted_entries)).encode("utf-8")).hexdigest()


def _finalize_folder_hashes(folder: _FolderDraft) -> None:
    for child_folder in folder.folders:
        _finalize_folder_hashes(child_folder)

    child_tokens: list[tuple[str, str]] = [("file", file_draft.fingerprint.content_id) for file_draft in folder.files]
    child_tokens.extend(("folder", child_folder.folder_content_hash) for child_folder in folder.folders)
    folder.folder_content_hash = folder_content_hash(child_tokens)

    manifest_entries: list[dict[str, object]] = []
    for file_draft in _flatten_files(folder):
        stat_result = file_draft.fingerprint.stat_after
        manifest_entries.append(
            {
                "type": "file",
                "relative_path": file_draft.relative_path,
                "name": file_draft.display_name,
                "size": file_draft.fingerprint.size_bytes,
                "ctime_ns": _ctime_ns(stat_result),
                "mtime_ns": _mtime_ns(stat_result),
                "atime_ns": _atime_ns(stat_result),
                "attrs": _file_attrs(stat_result),
                "content_id": file_draft.fingerprint.content_id,
            }
        )
    for child_folder in _flatten_folders(folder):
        stat_result = child_folder.stat_result
        manifest_entries.append(
            {
                "type": "folder",
                "relative_path": child_folder.relative_path,
                "name": child_folder.display_name,
                "size": 0,
                "ctime_ns": _ctime_ns(stat_result),
                "mtime_ns": _mtime_ns(stat_result),
                "atime_ns": _atime_ns(stat_result),
                "attrs": _file_attrs(stat_result),
                "content_id": child_folder.folder_content_hash,
            }
        )
    folder.folder_manifest_hash = folder_manifest_hash(manifest_entries)
    folder.child_file_count = len(folder.files)
    folder.child_folder_count = len(folder.folders)
    folder.total_file_count = len(_flatten_files(folder))
    folder.total_folder_count = len(_flatten_folders(folder)) - 1
    folder.total_issue_count = folder.direct_issue_count + sum(child.total_issue_count for child in folder.folders)


def _file_payload(
    file_draft: _FileDraft,
    *,
    backup_job_id: str,
    device_id: str,
    now: str,
    data_version: int,
) -> dict[str, object]:
    stat_result = file_draft.fingerprint.stat_after
    file_identity = read_file_identity(file_draft.local_path)
    return build_version_fields(
        entity_payload={
            "file_item_id": file_draft.file_item_id,
            "entity_id": f"file_item_{file_draft.file_item_id}",
            "backup_job_id": backup_job_id,
            "backup_source_id": file_draft.backup_source_id,
            "source_seq": file_draft.source_seq,
            "parent_folder_item_id": file_draft.parent_folder_item_id,
            "local_path": file_draft.local_path,
            "relative_path": file_draft.relative_path,
            "display_name": file_draft.display_name,
            "path_sha256": path_sha256(file_draft.local_path),
            "relative_path_sha256": path_sha256(file_draft.relative_path),
            "size_bytes": file_draft.fingerprint.size_bytes,
            "ctime_ns": _ctime_ns(stat_result),
            "mtime_ns": _mtime_ns(stat_result),
            "atime_ns": _atime_ns(stat_result),
            "file_attrs": _file_attrs(stat_result),
            "file_volume_serial": file_identity.volume_serial,
            "file_index": file_identity.file_index,
            "quick_fingerprint": file_draft.fingerprint.quick_fingerprint,
            "quick_sample_count": file_draft.fingerprint.quick_sample_count,
            "quick_sample_size": file_draft.fingerprint.quick_sample_size,
            "sample_plan_json": file_draft.fingerprint.sample_plan_json,
            "md5": file_draft.fingerprint.md5,
            "sha256": file_draft.fingerprint.sha256,
            "content_id": file_draft.fingerprint.content_id,
            "scan_status": file_draft.fingerprint.scan_status,
            "created_at": now,
        },
        updated_by_device_id=device_id,
        data_version=data_version,
        now=now,
    )


def _folder_payload(
    folder: _FolderDraft,
    *,
    backup_job_id: str,
    device_id: str,
    now: str,
    data_version: int,
) -> dict[str, object]:
    return build_version_fields(
        entity_payload={
            "folder_item_id": folder.folder_item_id,
            "entity_id": f"folder_item_{folder.folder_item_id}",
            "backup_job_id": backup_job_id,
            "backup_source_id": folder.backup_source_id,
            "source_seq": folder.source_seq,
            "parent_folder_item_id": folder.parent_folder_item_id,
            "local_path": folder.local_path,
            "relative_path": folder.relative_path,
            "display_name": folder.display_name,
            "path_sha256": path_sha256(folder.local_path),
            "relative_path_sha256": path_sha256(folder.relative_path),
            "ctime_ns": _ctime_ns(folder.stat_result),
            "mtime_ns": _mtime_ns(folder.stat_result),
            "atime_ns": _atime_ns(folder.stat_result),
            "file_attrs": _file_attrs(folder.stat_result),
            "child_file_count": folder.child_file_count,
            "child_folder_count": folder.child_folder_count,
            "total_file_count": folder.total_file_count,
            "total_folder_count": folder.total_folder_count,
            "folder_content_hash": folder.folder_content_hash,
            "folder_manifest_hash": folder.folder_manifest_hash,
            "scan_status": "partial" if folder.total_issue_count else "scanned",
            "created_at": now,
        },
        updated_by_device_id=device_id,
        data_version=data_version,
        now=now,
    )


def _issue_payload(issue: ScanIssue, *, backup_job_id: str, backup_source_id: str, now: str) -> dict[str, object]:
    return {
        "scan_issue_id": new_id("scan_issue"),
        "backup_job_id": backup_job_id,
        "backup_source_id": backup_source_id,
        "local_path": issue.local_path,
        "relative_path": issue.relative_path,
        "display_name": issue.display_name,
        "path_sha256": path_sha256(issue.local_path),
        "issue_type": issue.issue_type,
        "error_message": issue.error_message,
        "created_at": now,
    }


def _issue(path: Path, relative_path: str, issue_type: ScanIssueType, error_message: str = "") -> ScanIssue:
    return ScanIssue(
        local_path=str(path),
        relative_path=_normalize_relative_path(relative_path),
        display_name=path.name or str(path),
        issue_type=issue_type,
        error_message=error_message,
    )


def _flatten_files(folder: _FolderDraft) -> list[_FileDraft]:
    files = list(folder.files)
    for child in folder.folders:
        files.extend(_flatten_files(child))
    return files


def _flatten_folders(folder: _FolderDraft) -> list[_FolderDraft]:
    folders = [folder]
    for child in folder.folders:
        folders.extend(_flatten_folders(child))
    return folders


def _target_sample_count(size_bytes: int) -> int:
    if size_bytes <= 256 * MiB:
        return 4
    if size_bytes <= 1024 * MiB:
        return 8
    if size_bytes <= 8 * 1024 * MiB:
        return 16
    if size_bytes <= 64 * 1024 * MiB:
        return 32
    return 64


def _stable_item_id(prefix: str, backup_source_id: str, relative_path: str) -> str:
    digest = hashlib.sha256(f"{backup_source_id}\0{relative_path}".encode("utf-8")).hexdigest()
    return f"{prefix}_{digest}"


def _safe_lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except OSError:
        return None


def _file_attrs(stat_result: os.stat_result) -> int:
    return int(getattr(stat_result, "st_file_attributes", 0) or 0)


def _ctime_ns(stat_result: os.stat_result) -> int:
    return int(getattr(stat_result, "st_ctime_ns", int(stat_result.st_ctime * 1_000_000_000)))


def _mtime_ns(stat_result: os.stat_result) -> int:
    return int(getattr(stat_result, "st_mtime_ns", int(stat_result.st_mtime * 1_000_000_000)))


def _atime_ns(stat_result: os.stat_result) -> int:
    return int(getattr(stat_result, "st_atime_ns", int(stat_result.st_atime * 1_000_000_000)))


def _join_relative(parent: str, child_name: str) -> str:
    return _normalize_relative_path(f"{parent}/{child_name}" if parent else child_name)


def _normalize_relative_path(value: str) -> str:
    return value.strip().replace("\\", "/").strip("/")
