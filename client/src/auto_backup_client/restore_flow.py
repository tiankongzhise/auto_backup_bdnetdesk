from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol

from auto_backup_client import local_fs
from auto_backup_client.archive_packager import file_sha256, resolve_7zip_executable
from auto_backup_client.backup_jobs import path_sha256
from auto_backup_client.baidu.upload import BaiduNetdiskClient
from auto_backup_client.cache_artifacts import CacheArtifactManager, build_job_cache_dir
from auto_backup_client.sqlite_store import SQLiteClientStore, build_version_fields, new_id, utc_now_iso
from auto_backup_client.subprocess_utils import hidden_subprocess_kwargs


RestoreTargetMode = Literal["original_path", "manual_path"]
RestoreConflictStrategy = Literal["keep_both", "skip_existing", "overwrite"]
ArchiveSource = Literal["local_cache", "downloaded", "not_available"]


@dataclass(frozen=True)
class RestoreCandidate:
    restore_candidate_id: str
    backup_source_id: str
    content_reference_id: str
    file_item_id: str
    backup_job_id: str
    job_name: str
    job_status: str
    device_id: str
    source_type: str
    source_display_name: str
    display_name: str
    original_path: str
    relative_path: str
    path_sha256: str
    size_bytes: int
    sha256: str
    content_id: str
    cleanup_status: str
    restore_status: str
    archive_id: str
    archive_seq: int
    archive_sha256: str
    archive_size: int
    archive_type: str
    archive_member_path: str
    archive_verify_status: str
    local_archive_path: str
    manifest_sha256: str
    remote_archive_path: str
    remote_archive_fs_id: int
    remote_archive_status: str
    candidate_status: str
    reason: str
    file_count: int = 1

    @property
    def local_archive_available(self) -> bool:
        return self.candidate_status == "ready_local"

    @property
    def remote_download_available(self) -> bool:
        return self.candidate_status == "needs_download"

    @property
    def restorable(self) -> bool:
        return self.candidate_status in {"ready_local", "needs_download"}


@dataclass(frozen=True)
class RestoreCandidateReport:
    candidates: tuple[RestoreCandidate, ...]
    restorable_count: int
    local_ready_count: int
    needs_download_count: int
    blocked_count: int


@dataclass(frozen=True)
class RestoreItemResult:
    content_reference_id: str
    restore_record_id: str
    status: str
    target_path_sha256: str
    final_path_sha256: str
    archive_source: ArchiveSource
    restored_sha256: str = ""
    error_code: str = ""
    error_message: str = ""


@dataclass(frozen=True)
class RestoreApplyResult:
    requested_count: int
    restored_count: int
    skipped_count: int
    failed_count: int
    results: tuple[RestoreItemResult, ...]


class ArchiveDownloader(Protocol):
    def download_archive(self, candidate: RestoreCandidate, target_path: Path) -> None:
        ...


class RestoreFlowError(ValueError):
    pass


class BaiduArchiveDownloader:
    def __init__(self, baidu: BaiduNetdiskClient) -> None:
        self._baidu = baidu

    def download_archive(self, candidate: RestoreCandidate, target_path: Path) -> None:
        if candidate.remote_archive_fs_id <= 0:
            raise RestoreFlowError("remote archive fs_id is required for download")
        metas = self._baidu.file_metas((candidate.remote_archive_fs_id,), dlink=True)
        matched = next((item for item in metas.items if item.fs_id == candidate.remote_archive_fs_id), None)
        if matched is None or not matched.dlink:
            raise RestoreFlowError("baidu filemetas did not return archive dlink")
        if matched.size and matched.size != candidate.archive_size:
            raise RestoreFlowError("baidu filemetas size does not match local archive record")
        self._baidu.download_dlink(matched.dlink, target_path)


class SevenZipRestoreRunner:
    def __init__(self, executable: str | Path | None = None) -> None:
        self.executable = resolve_7zip_executable(executable)

    def test_archive(self, *, archive_path: Path, password: str) -> None:
        self._run(
            "test",
            [str(self.executable), "t", f"-p{password}", str(archive_path)],
            cwd=archive_path.parent,
        )

    def extract_all(self, *, archive_path: Path, output_dir: Path, password: str) -> None:
        self._run(
            "extract",
            [str(self.executable), "x", "-y", f"-p{password}", str(archive_path), f"-o{output_dir}"],
            cwd=archive_path.parent,
        )

    def _run(self, action: str, args: list[str], *, cwd: Path) -> None:
        completed = subprocess.run(
            args,
            cwd=str(cwd),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            **hidden_subprocess_kwargs(),
        )
        if completed.returncode != 0:
            raise RestoreFlowError(f"7-Zip {action} failed")


class RestoreService:
    def __init__(
        self,
        store: SQLiteClientStore,
        *,
        device_id: str,
        cache_root: str | Path,
        runner: SevenZipRestoreRunner | None = None,
        artifact_manager: CacheArtifactManager | None = None,
        downloader: ArchiveDownloader | None = None,
    ) -> None:
        cleaned_device_id = device_id.strip()
        if not cleaned_device_id:
            raise RestoreFlowError("device_id is required")
        self.store = store
        self.device_id = cleaned_device_id
        self.cache_root = Path(cache_root).expanduser().resolve()
        self.runner = runner or SevenZipRestoreRunner()
        self.artifact_manager = artifact_manager
        self.downloader = downloader

    def list_candidates(self, *, backup_job_id: str = "", keyword: str = "", limit: int = 500) -> RestoreCandidateReport:
        if limit < 1 or limit > 5000:
            raise RestoreFlowError("restore candidate limit must be between 1 and 5000")
        rows = self._candidate_rows(
            backup_job_id=backup_job_id.strip(),
            keyword=" ".join(keyword.strip().split()).casefold(),
            limit=limit,
        )
        candidates = tuple(_candidate_from_row(row) for row in rows)
        return RestoreCandidateReport(
            candidates=candidates,
            restorable_count=sum(1 for candidate in candidates if candidate.restorable),
            local_ready_count=sum(1 for candidate in candidates if candidate.local_archive_available),
            needs_download_count=sum(1 for candidate in candidates if candidate.remote_download_available),
            blocked_count=sum(1 for candidate in candidates if not candidate.restorable),
        )

    def restore(
        self,
        *,
        backup_job_id: str = "",
        content_reference_ids: tuple[str, ...] = (),
        target_mode: RestoreTargetMode = "manual_path",
        target_root: str | Path | None = None,
        password: str,
        conflict_strategy: RestoreConflictStrategy = "keep_both",
        now: str | None = None,
    ) -> RestoreApplyResult:
        if not password:
            raise RestoreFlowError("archive password is required")
        cleaned_target_mode = _validate_target_mode(target_mode)
        cleaned_conflict_strategy = _validate_conflict_strategy(conflict_strategy)
        if cleaned_conflict_strategy == "overwrite":
            raise RestoreFlowError("overwrite restore is not implemented because recycle-bin protection is required")
        if cleaned_target_mode == "manual_path" and target_root is None:
            raise RestoreFlowError("target_root is required for manual_path restore")
        selected_ids = tuple(dict.fromkeys(value.strip() for value in content_reference_ids if value.strip()))
        report = self.list_candidates(backup_job_id=backup_job_id, limit=5000)
        candidates = tuple(candidate for candidate in report.candidates if not selected_ids or candidate.restore_candidate_id in selected_ids)
        if selected_ids and len(candidates) != len(selected_ids):
            raise RestoreFlowError("one or more selected restore references were not found")
        if not candidates:
            raise RestoreFlowError("no restore candidates selected")

        actual_now = now or utc_now_iso()
        results: list[RestoreItemResult] = []
        for candidate in candidates:
            target_path = _target_root_path(candidate, target_mode=cleaned_target_mode, target_root=target_root)
            archive_path: Path | None = None
            archive_source: ArchiveSource = "not_available"
            restore_dir = self._restore_dir(candidate, actual_now)
            try:
                if not candidate.restorable:
                    raise RestoreFlowError(candidate.reason)
                archive_path, archive_source = self._resolve_archive(candidate, now=actual_now)
                if file_sha256(archive_path) != candidate.archive_sha256:
                    raise RestoreFlowError("archive sha256 mismatch")
                local_fs.remove_tree(restore_dir)
                local_fs.make_dirs(restore_dir)
                self._register_artifact(restore_dir, job_id=candidate.backup_job_id, now=actual_now)
                extracted_root = self._extract_archive(candidate.archive_id, archive_path, restore_dir, password=password)
                manifest = _load_manifest(extracted_root, expected_sha256=candidate.manifest_sha256)
                items = _manifest_items_for_candidate(manifest, candidate)
                if not items:
                    raise RestoreFlowError("selected source is missing from archive manifest")
                for item in items:
                    item_candidate = _candidate_for_manifest_item(candidate, item)
                    item_target_path = _target_path(item_candidate, target_mode=cleaned_target_mode, target_root=target_root)
                    payload_path = self._payload_path(
                        item,
                        current_archive_id=candidate.archive_id,
                        current_extract_root=extracted_root,
                        restore_dir=restore_dir,
                        password=password,
                        now=actual_now,
                    )
                    if file_sha256(payload_path) != item_candidate.sha256:
                        raise RestoreFlowError("restored payload sha256 does not match manifest")
                    final_path, skipped = _resolve_conflict(item_target_path, cleaned_conflict_strategy, now=actual_now)
                    if skipped:
                        results.append(
                            self._write_record(
                                item_candidate,
                                status="skipped_existing",
                                target_mode=cleaned_target_mode,
                                conflict_strategy=cleaned_conflict_strategy,
                                archive_source=archive_source,
                                target_path=item_target_path,
                                final_path=item_target_path,
                                archive_path=archive_path,
                                manifest_sha256=candidate.manifest_sha256,
                                restored_sha256="",
                                error_code="",
                                error_message="",
                                now=actual_now,
                                reference_restore_status="not_restored",
                            )
                        )
                        continue
                    local_fs.make_dirs(final_path.parent)
                    with local_fs.open_file(payload_path, "rb") as src, local_fs.open_file(final_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    restored_sha256 = file_sha256(final_path)
                    if restored_sha256 != item_candidate.sha256:
                        local_fs.unlink(final_path, missing_ok=True)
                        raise RestoreFlowError("restored file sha256 mismatch after copy")
                    results.append(
                        self._write_record(
                            item_candidate,
                            status="restored",
                            target_mode=cleaned_target_mode,
                            conflict_strategy=cleaned_conflict_strategy,
                            archive_source=archive_source,
                            target_path=item_target_path,
                            final_path=final_path,
                            archive_path=archive_path,
                            manifest_sha256=candidate.manifest_sha256,
                            restored_sha256=restored_sha256,
                            error_code="",
                            error_message="",
                            now=actual_now,
                            reference_restore_status="restored",
                        )
                    )
            except Exception as exc:
                results.append(
                    self._write_record(
                        candidate,
                        status="failed",
                        target_mode=cleaned_target_mode,
                        conflict_strategy=cleaned_conflict_strategy,
                        archive_source=archive_source,
                        target_path=target_path,
                        final_path=target_path,
                        archive_path=archive_path,
                        manifest_sha256=candidate.manifest_sha256,
                        restored_sha256="",
                        error_code=_error_code(exc),
                        error_message=_safe_error(exc),
                        now=actual_now,
                        reference_restore_status="restore_failed",
                    )
                )
            finally:
                local_fs.remove_tree(restore_dir)
                self._mark_artifact_deleted(restore_dir, now=actual_now)

        return RestoreApplyResult(
            requested_count=len(candidates),
            restored_count=sum(1 for result in results if result.status == "restored"),
            skipped_count=sum(1 for result in results if result.status == "skipped_existing"),
            failed_count=sum(1 for result in results if result.status == "failed"),
            results=tuple(results),
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
                    OR lower(bs.display_name) LIKE ?
                    OR lower(bs.local_path) LIKE ?
                    OR lower(a.archive_sha256) LIKE ?
                    OR lower(ro.remote_path) LIKE ?
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
                    j.device_id,
                    bs.source_type,
                    bs.display_name AS source_display_name,
                    bs.backup_source_id,
                    MIN(cr.content_reference_id) AS content_reference_id,
                    MIN(cr.file_item_id) AS file_item_id,
                    bs.local_path,
                    '' AS relative_path,
                    bs.path_sha256,
                    bs.display_name,
                    COALESCE(SUM(cr.size_bytes), 0) AS size_bytes,
                    COALESCE(MAX(cr.file_sha256), '') AS file_sha256,
                    COALESCE(MAX(cr.content_id), '') AS content_id,
                    CASE
                        WHEN SUM(CASE WHEN cr.cleanup_status = 'cleaned' THEN 1 ELSE 0 END) = COUNT(cr.content_reference_id) THEN 'cleaned'
                        WHEN SUM(CASE WHEN cr.cleanup_status = 'not_cleaned' THEN 1 ELSE 0 END) = COUNT(cr.content_reference_id) THEN 'not_cleaned'
                        ELSE 'mixed'
                    END AS cleanup_status,
                    CASE
                        WHEN SUM(CASE WHEN cr.restore_status = 'restored' THEN 1 ELSE 0 END) = COUNT(cr.content_reference_id) THEN 'restored'
                        WHEN SUM(CASE WHEN cr.restore_status = 'not_restored' THEN 1 ELSE 0 END) = COUNT(cr.content_reference_id) THEN 'not_restored'
                        ELSE 'mixed'
                    END AS restore_status,
                    cr.archive_id,
                    cr.archive_sha256,
                    '' AS archive_member_path,
                    a.archive_seq,
                    a.archive_size,
                    a.archive_type,
                    a.verify_status AS archive_verify_status,
                    a.local_archive_path,
                    a.manifest_sha256,
                    ro.remote_path AS remote_archive_path,
                    ro.fs_id AS remote_archive_fs_id,
                    ro.status AS remote_archive_status,
                    COUNT(cr.content_reference_id) AS file_count
                FROM content_references cr
                JOIN backup_jobs j ON j.backup_job_id = cr.backup_job_id
                JOIN backup_sources bs ON bs.backup_source_id = cr.backup_source_id
                LEFT JOIN archives a ON a.archive_id = cr.archive_id
                LEFT JOIN remote_objects ro
                    ON ro.archive_id = cr.archive_id
                   AND ro.object_type = 'archive'
                {where_sql}
                GROUP BY
                    j.backup_job_id,
                    j.job_name,
                    j.status,
                    j.device_id,
                    bs.backup_source_id,
                    bs.source_seq,
                    bs.source_type,
                    bs.display_name,
                    bs.local_path,
                    bs.path_sha256,
                    cr.archive_id,
                    cr.archive_sha256,
                    a.archive_seq,
                    a.archive_size,
                    a.archive_type,
                    a.verify_status,
                    a.local_archive_path,
                    a.manifest_sha256,
                    ro.remote_path,
                    ro.fs_id,
                    ro.status
                ORDER BY j.created_at DESC, bs.source_seq, bs.backup_source_id
                LIMIT ?
                """,
                tuple(params + [limit]),
            ).fetchall()
            return [dict(row) for row in rows]

    def _resolve_archive(self, candidate: RestoreCandidate, *, now: str) -> tuple[Path, ArchiveSource]:
        local_path = Path(candidate.local_archive_path) if candidate.local_archive_path else Path()
        if candidate.local_archive_path and local_fs.is_file(local_path):
            return local_path, "local_cache"
        if self.downloader is None:
            raise RestoreFlowError("archive is not available locally and no downloader is configured")
        target = self._download_archive_path(candidate)
        self.downloader.download_archive(candidate, target)
        if file_sha256(target) != candidate.archive_sha256:
            local_fs.unlink(target, missing_ok=True)
            raise RestoreFlowError("downloaded archive sha256 mismatch")
        self._register_download_artifact(target, candidate, now=now)
        return target, "downloaded"

    def _payload_path(
        self,
        item: Mapping[str, Any],
        *,
        current_archive_id: str,
        current_extract_root: Path,
        restore_dir: Path,
        password: str,
        now: str,
    ) -> Path:
        member_path = _safe_archive_member_path(str(item.get("archive_member_path") or ""))
        if member_path:
            current_payload = current_extract_root / member_path
            if local_fs.is_file(current_payload):
                return current_payload
        content_id = str(item.get("content_id") or "")
        external = self._external_payload_source(content_id=content_id, current_archive_id=current_archive_id)
        if external is None:
            raise RestoreFlowError("manifest references payload that is not available in current or local external archive")
        archive_path, _source = self._resolve_archive(external, now=now)
        extracted = self._extract_archive(external.archive_id, archive_path, restore_dir, password=password)
        external_member = _safe_archive_member_path(external.archive_member_path or f"payload/{content_id}")
        payload = extracted / external_member
        if not local_fs.is_file(payload):
            raise RestoreFlowError("external archive payload member is missing")
        return payload

    def _external_payload_source(self, *, content_id: str, current_archive_id: str) -> RestoreCandidate | None:
        if not content_id:
            return None
        with self.store.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    j.backup_job_id,
                    j.job_name,
                    j.status AS job_status,
                    j.device_id,
                    bs.source_type,
                    bs.display_name AS source_display_name,
                    cr.content_reference_id,
                    cr.file_item_id,
                    cr.local_path,
                    cr.relative_path,
                    cr.path_sha256,
                    cr.display_name,
                    cr.size_bytes,
                    cr.file_sha256,
                    cr.content_id,
                    cr.cleanup_status,
                    cr.restore_status,
                    cr.archive_id,
                    cr.archive_sha256,
                    cr.archive_member_path,
                    a.archive_seq,
                    a.archive_size,
                    a.archive_type,
                    a.verify_status AS archive_verify_status,
                    a.local_archive_path,
                    a.manifest_sha256,
                    ro.remote_path AS remote_archive_path,
                    ro.fs_id AS remote_archive_fs_id,
                    ro.status AS remote_archive_status
                FROM archive_members am
                JOIN content_references cr ON cr.content_reference_id = am.content_reference_id
                JOIN backup_jobs j ON j.backup_job_id = cr.backup_job_id
                JOIN backup_sources bs ON bs.backup_source_id = cr.backup_source_id
                JOIN archives a ON a.archive_id = am.archive_id
                LEFT JOIN remote_objects ro
                    ON ro.archive_id = am.archive_id
                   AND ro.object_type = 'archive'
                WHERE am.content_id = ?
                  AND am.member_type = 'payload'
                  AND am.archive_id <> ?
                ORDER BY a.created_at DESC, am.archive_member_id
                LIMIT 1
                """,
                (content_id, current_archive_id),
            ).fetchone()
        return _candidate_from_row(dict(row)) if row is not None else None

    def _extract_archive(self, archive_id: str, archive_path: Path, restore_dir: Path, *, password: str) -> Path:
        extract_root = restore_dir / _safe_dir_name(archive_id)
        if local_fs.is_dir(extract_root):
            return extract_root
        local_fs.make_dirs(extract_root)
        self.runner.test_archive(archive_path=archive_path, password=password)
        self.runner.extract_all(archive_path=archive_path, output_dir=extract_root, password=password)
        return extract_root

    def _restore_dir(self, candidate: RestoreCandidate, now: str) -> Path:
        digest = hashlib.sha256(f"{candidate.content_reference_id}\0{now}".encode("utf-8")).hexdigest()[:16]
        return build_job_cache_dir(self.cache_root, candidate.backup_job_id) / "restore" / digest

    def _download_archive_path(self, candidate: RestoreCandidate) -> Path:
        job_cache = build_job_cache_dir(self.cache_root, candidate.backup_job_id)
        filename = f"{candidate.archive_seq:06d}-{candidate.archive_sha256}.7z"
        return job_cache / "download" / _safe_dir_name(candidate.archive_id) / filename

    def _register_artifact(self, path: Path, *, job_id: str, now: str) -> None:
        if self.artifact_manager is None:
            return
        self.artifact_manager.register_path(
            path=path,
            artifact_type="restore",
            job_id=job_id,
            required_until_stage="restore_completed",
            now=now,
        )

    def _register_download_artifact(self, path: Path, candidate: RestoreCandidate, *, now: str) -> None:
        if self.artifact_manager is None:
            return
        self.artifact_manager.register_path(
            path=path,
            artifact_type="download",
            job_id=candidate.backup_job_id,
            required_until_stage="restore_completed",
            remote_confirmed=True,
            now=now,
        )

    def _mark_artifact_deleted(self, path: Path, *, now: str) -> None:
        if self.artifact_manager is None:
            return
        artifact_id = f"artifact_{hashlib.sha256(str(path.resolve()).encode('utf-8')).hexdigest()}"
        with self.store.transaction() as conn:
            self.store.update_cache_artifact_status(
                conn,
                artifact_id=artifact_id,
                lifecycle_status="deleted",
                size_bytes=0,
                deleted_at=now,
                last_accessed_at=now,
            )

    def _write_record(
        self,
        candidate: RestoreCandidate,
        *,
        status: str,
        target_mode: RestoreTargetMode,
        conflict_strategy: RestoreConflictStrategy,
        archive_source: ArchiveSource,
        target_path: Path,
        final_path: Path,
        archive_path: Path | None,
        manifest_sha256: str,
        restored_sha256: str,
        error_code: str,
        error_message: str,
        now: str,
        reference_restore_status: str,
    ) -> RestoreItemResult:
        record_id = new_id("restore")
        final_path_text = str(final_path)
        archive_path_text = str(archive_path) if archive_path is not None else ""
        payload = build_version_fields(
            entity_payload={
                "restore_record_id": record_id,
                "entity_id": f"restore_record_{record_id}",
                "backup_job_id": candidate.backup_job_id,
                "content_reference_id": candidate.content_reference_id,
                "file_item_id": candidate.file_item_id,
                "archive_id": candidate.archive_id,
                "archive_sha256": candidate.archive_sha256,
                "device_id": candidate.device_id,
                "display_name": candidate.display_name,
                "restore_status": status,
                "restore_target_mode": target_mode,
                "conflict_strategy": conflict_strategy,
                "archive_source": archive_source,
                "target_path": str(target_path),
                "target_path_sha256": path_sha256(str(target_path)),
                "final_path": final_path_text,
                "final_path_sha256": path_sha256(final_path_text) if final_path_text else "",
                "archive_path": archive_path_text,
                "archive_path_sha256": path_sha256(archive_path_text) if archive_path_text else "",
                "manifest_sha256": manifest_sha256,
                "expected_sha256": candidate.sha256,
                "restored_sha256": restored_sha256,
                "size_bytes": candidate.size_bytes,
                "restore_time": now if status in {"restored", "skipped_existing"} else None,
                "error_code": error_code,
                "error_message": error_message,
                "created_at": now,
            },
            updated_by_device_id=self.device_id,
            now=now,
            sync_status="sync_pending",
        )
        with self.store.transaction() as conn:
            self.store.put_restore_record(conn, payload)
            self.store.update_content_reference_restore_status(
                conn,
                content_reference_id=candidate.content_reference_id,
                restore_status=reference_restore_status,
                updated_at=now,
                updated_by_device_id=self.device_id,
            )
        return RestoreItemResult(
            content_reference_id=candidate.content_reference_id,
            restore_record_id=record_id,
            status=status,
            target_path_sha256=path_sha256(str(target_path)),
            final_path_sha256=path_sha256(final_path_text) if final_path_text else "",
            archive_source=archive_source,
            restored_sha256=restored_sha256,
            error_code=error_code,
            error_message=error_message,
        )


def _candidate_from_row(row: Mapping[str, object]) -> RestoreCandidate:
    status, reason = _candidate_status(row)
    return RestoreCandidate(
        restore_candidate_id=str(row.get("backup_source_id") or row.get("content_reference_id") or ""),
        backup_source_id=str(row.get("backup_source_id") or ""),
        content_reference_id=str(row["content_reference_id"]),
        file_item_id=str(row["file_item_id"]),
        backup_job_id=str(row["backup_job_id"]),
        job_name=str(row["job_name"]),
        job_status=str(row["job_status"]),
        device_id=str(row["device_id"]),
        source_type=str(row["source_type"] or ""),
        source_display_name=str(row["source_display_name"] or ""),
        display_name=str(row["display_name"]),
        original_path=str(row["local_path"]),
        relative_path=str(row["relative_path"]),
        path_sha256=str(row["path_sha256"]),
        size_bytes=int(row["size_bytes"] or 0),
        sha256=str(row["file_sha256"]),
        content_id=str(row["content_id"] or ""),
        cleanup_status=str(row["cleanup_status"] or "not_cleaned"),
        restore_status=str(row["restore_status"] or "not_restored"),
        archive_id=str(row["archive_id"] or ""),
        archive_seq=int(row["archive_seq"] or 0),
        archive_sha256=str(row["archive_sha256"] or ""),
        archive_size=int(row["archive_size"] or 0),
        archive_type=str(row["archive_type"] or ""),
        archive_member_path=str(row["archive_member_path"] or ""),
        archive_verify_status=str(row["archive_verify_status"] or ""),
        local_archive_path=str(row["local_archive_path"] or ""),
        manifest_sha256=str(row["manifest_sha256"] or ""),
        remote_archive_path=str(row["remote_archive_path"] or ""),
        remote_archive_fs_id=int(row["remote_archive_fs_id"] or 0),
        remote_archive_status=str(row["remote_archive_status"] or ""),
        candidate_status=status,
        reason=reason,
        file_count=int(row.get("file_count") or 1),
    )


def _candidate_status(row: Mapping[str, object]) -> tuple[str, str]:
    if str(row["job_status"] or "") != "completed":
        return "not_completed", "backup job is not completed"
    if not str(row["archive_id"] or ""):
        return "not_packaged", "source has no archive assignment"
    if str(row["archive_verify_status"] or "") != "standard_test_passed":
        return "not_verified", "archive standard verification is not passed"
    archive_sha256 = str(row["archive_sha256"] or "")
    if len(archive_sha256) != 64:
        return "not_verified", "archive sha256 is missing"
    local_archive_path = str(row["local_archive_path"] or "")
    if local_archive_path and local_fs.is_file(local_archive_path):
        return "ready_local", "archive is available in local cache"
    if str(row["remote_archive_status"] or "") == "remote_created" and int(row["remote_archive_fs_id"] or 0) > 0:
        return "needs_download", "archive is missing locally but available on Baidu"
    return "archive_unavailable", "archive is not available locally and no confirmed remote object is recorded"


def _load_manifest(extract_root: Path, *, expected_sha256: str) -> dict[str, Any]:
    manifest_path = extract_root / "manifest" / "manifest.json"
    if not local_fs.is_file(manifest_path):
        raise RestoreFlowError("archive manifest is missing")
    actual_sha256 = file_sha256(manifest_path)
    if expected_sha256 and actual_sha256 != expected_sha256:
        raise RestoreFlowError("archive manifest sha256 mismatch")
    with local_fs.open_file(manifest_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise RestoreFlowError("archive manifest must contain items")
    return data


def _manifest_items_for_candidate(manifest: Mapping[str, Any], candidate: RestoreCandidate) -> tuple[Mapping[str, Any], ...]:
    matches: list[Mapping[str, Any]] = []
    for item in manifest["items"]:
        if not isinstance(item, Mapping) or str(item.get("item_type", "")) != "file":
            continue
        if candidate.backup_source_id and str(item.get("backup_source_id", "")) == candidate.backup_source_id:
            matches.append(item)
            continue
        if str(item.get("content_reference_id", "")) == candidate.content_reference_id:
            matches.append(item)
            continue
        if str(item.get("item_id", "")) == candidate.file_item_id:
            matches.append(item)
    return tuple(matches)


def _candidate_for_manifest_item(candidate: RestoreCandidate, item: Mapping[str, Any]) -> RestoreCandidate:
    relative_path = str(item.get("relative_path") or candidate.relative_path)
    display_name = str(item.get("display_name") or item.get("original_name") or Path(relative_path).name or candidate.display_name)
    file_sha256 = str(item.get("sha256") or item.get("file_sha256") or candidate.sha256)
    content_reference_id = str(item.get("content_reference_id") or candidate.content_reference_id)
    file_item_id = str(item.get("file_item_id") or item.get("item_id") or candidate.file_item_id)
    content_id = str(item.get("content_id") or candidate.content_id)
    size_bytes = int(item.get("size_bytes") or item.get("size") or candidate.size_bytes or 0)
    archive_member_path = str(item.get("archive_member_path") or candidate.archive_member_path)
    path_digest = str(item.get("path_sha256") or path_sha256(str(Path(candidate.original_path).parent / relative_path)))
    original_path = str(item.get("local_path") or item.get("original_path") or candidate.original_path)
    return RestoreCandidate(
        restore_candidate_id=candidate.restore_candidate_id,
        backup_source_id=candidate.backup_source_id,
        content_reference_id=content_reference_id,
        file_item_id=file_item_id,
        backup_job_id=candidate.backup_job_id,
        job_name=candidate.job_name,
        job_status=candidate.job_status,
        device_id=candidate.device_id,
        source_type=candidate.source_type,
        source_display_name=candidate.source_display_name,
        display_name=display_name,
        original_path=original_path,
        relative_path=relative_path,
        path_sha256=path_digest,
        size_bytes=size_bytes,
        sha256=file_sha256,
        content_id=content_id,
        cleanup_status=candidate.cleanup_status,
        restore_status=candidate.restore_status,
        archive_id=candidate.archive_id,
        archive_seq=candidate.archive_seq,
        archive_sha256=candidate.archive_sha256,
        archive_size=candidate.archive_size,
        archive_type=candidate.archive_type,
        archive_member_path=archive_member_path,
        archive_verify_status=candidate.archive_verify_status,
        local_archive_path=candidate.local_archive_path,
        manifest_sha256=candidate.manifest_sha256,
        remote_archive_path=candidate.remote_archive_path,
        remote_archive_fs_id=candidate.remote_archive_fs_id,
        remote_archive_status=candidate.remote_archive_status,
        candidate_status=candidate.candidate_status,
        reason=candidate.reason,
        file_count=1,
    )


def _target_root_path(candidate: RestoreCandidate, *, target_mode: RestoreTargetMode, target_root: str | Path | None) -> Path:
    if target_mode == "original_path":
        return Path(candidate.original_path).expanduser()
    root = Path(target_root or "").expanduser()
    if not str(root).strip():
        raise RestoreFlowError("target_root is required for manual_path restore")
    if candidate.source_type == "directory":
        return root / _safe_relative_path(candidate.source_display_name or candidate.display_name)
    return root / _safe_relative_path(candidate.display_name)


def _target_path(candidate: RestoreCandidate, *, target_mode: RestoreTargetMode, target_root: str | Path | None) -> Path:
    if target_mode == "original_path":
        return Path(candidate.original_path).expanduser()
    root = Path(target_root or "").expanduser()
    if not str(root).strip():
        raise RestoreFlowError("target_root is required for manual_path restore")
    relative_value = candidate.relative_path or candidate.display_name
    if candidate.source_type == "directory":
        relative_value = "/".join(part for part in (candidate.source_display_name, relative_value) if part)
    relative = _safe_relative_path(relative_value)
    return root / relative


def _resolve_conflict(target_path: Path, strategy: RestoreConflictStrategy, *, now: str) -> tuple[Path, bool]:
    if not local_fs.exists(target_path):
        return target_path, False
    if strategy == "skip_existing":
        return target_path, True
    if strategy != "keep_both":
        raise RestoreFlowError("unsupported restore conflict strategy")
    stamp = _timestamp_suffix(now)
    stem = target_path.stem or target_path.name
    suffix = target_path.suffix
    parent = target_path.parent
    candidate = parent / f"{stem} restored {stamp}{suffix}"
    index = 2
    while local_fs.exists(candidate):
        candidate = parent / f"{stem} restored {stamp}-{index}{suffix}"
        index += 1
    return candidate, False


def _safe_relative_path(value: str) -> Path:
    cleaned = str(value).replace("\\", "/").strip("/")
    if not cleaned:
        raise RestoreFlowError("relative path is required for manual restore")
    parts = tuple(part for part in cleaned.split("/") if part)
    if any(part in {".", ".."} for part in parts):
        raise RestoreFlowError("relative path contains unsafe segment")
    candidate = Path(*parts)
    if candidate.is_absolute():
        raise RestoreFlowError("relative path must not be absolute")
    return candidate


def _safe_archive_member_path(value: str) -> Path:
    cleaned = value.replace("\\", "/").strip("/")
    if not cleaned:
        return Path()
    parts = tuple(part for part in cleaned.split("/") if part)
    if any(part in {".", ".."} for part in parts):
        raise RestoreFlowError("archive member path contains unsafe segment")
    return Path(*parts)


def _timestamp_suffix(now: str) -> str:
    normalized = now.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = datetime.now(timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _safe_dir_name(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return digest[:16]


def _validate_target_mode(value: str) -> RestoreTargetMode:
    if value not in {"original_path", "manual_path"}:
        raise RestoreFlowError("unsupported restore target mode")
    return value  # type: ignore[return-value]


def _validate_conflict_strategy(value: str) -> RestoreConflictStrategy:
    if value not in {"keep_both", "skip_existing", "overwrite"}:
        raise RestoreFlowError("unsupported restore conflict strategy")
    return value  # type: ignore[return-value]


def _error_code(exc: Exception) -> str:
    text = str(exc).casefold()
    if "7-zip" in text:
        return "archive_password_or_extract_failed"
    if "sha256" in text:
        return "hash_mismatch"
    if "manifest" in text:
        return "manifest_error"
    if "not available" in text or "unavailable" in text:
        return "archive_unavailable"
    if isinstance(exc, OSError):
        return "filesystem_error"
    return "restore_failed"


def _safe_error(exc: Exception) -> str:
    text = str(exc)
    if len(text) > 300:
        text = text[:297] + "..."
    return text.replace("\n", " ").replace("\r", " ")
