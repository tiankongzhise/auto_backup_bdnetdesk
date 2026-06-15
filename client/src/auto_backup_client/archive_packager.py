from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from auto_backup_client import local_fs
from auto_backup_client.cache_artifacts import CacheArtifactManager, build_job_cache_dir
from auto_backup_client.sqlite_store import (
    SQLiteClientStore,
    build_version_fields,
    new_id,
    stable_json_dumps,
    utc_now_iso,
)
from auto_backup_client.subprocess_utils import hidden_subprocess_kwargs


MANIFEST_VERSION = 1
DEFAULT_SEVEN_ZIP_CANDIDATES = (
    Path("C:/Program Files/7-Zip/7z.exe"),
    Path("C:/Program Files (x86)/7-Zip/7z.exe"),
)
SECRET_KEY_HINTS = (
    "access_token",
    "refresh_token",
    "device_token",
    "wrapping_key",
    "password",
    "secret",
    "token_envelope",
)


@dataclass(frozen=True)
class ArchivePackageResult:
    backup_job_id: str
    archive_id: str
    archive_seq: int
    archive_path: Path
    archive_sha256: str
    archive_md5: str
    archive_size: int
    archive_type: str
    manifest_id: str
    manifest_sha256: str
    manifest_size: int
    manifest_item_count: int
    payload_member_count: int
    reference_member_count: int


class ArchivePackagingError(ValueError):
    pass


class SevenZipNotFoundError(ArchivePackagingError):
    pass


class PayloadSourceChangedError(ArchivePackagingError):
    def __init__(self, *, file_item_id: str, display_name: str) -> None:
        self.file_item_id = file_item_id
        self.display_name = display_name
        super().__init__(f"payload source file changed after scan: {display_name}")


class SevenZipRunner:
    def __init__(self, executable: str | Path | None = None) -> None:
        self.executable = resolve_7zip_executable(executable)

    def create_archive(self, *, archive_path: Path, staging_dir: Path, password: str) -> None:
        entries = ["manifest"]
        if (staging_dir / "payload").is_dir() and any((staging_dir / "payload").iterdir()):
            entries.append("payload")
        self._run(
            "add",
            [
                str(self.executable),
                "a",
                "-t7z",
                "-m0=LZMA2",
                "-mhe=on",
                f"-p{password}",
                str(archive_path),
                *entries,
            ],
            cwd=staging_dir,
        )

    def test_archive(self, *, archive_path: Path, password: str) -> None:
        self._run(
            "test",
            [str(self.executable), "t", f"-p{password}", str(archive_path)],
            cwd=archive_path.parent,
        )

    def extract_manifest(self, *, archive_path: Path, output_dir: Path, password: str) -> Path:
        self._run(
            "extract_manifest",
            [
                str(self.executable),
                "x",
                "-y",
                f"-p{password}",
                str(archive_path),
                "manifest/manifest.json",
                f"-o{output_dir}",
            ],
            cwd=archive_path.parent,
        )
        manifest_path = output_dir / "manifest" / "manifest.json"
        if not manifest_path.is_file():
            raise ArchivePackagingError("7-Zip validation did not extract manifest/manifest.json")
        return manifest_path

    def _run(self, action: str, args: Sequence[str], *, cwd: Path) -> None:
        completed = subprocess.run(
            list(args),
            cwd=str(cwd),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            **hidden_subprocess_kwargs(),
        )
        if completed.returncode != 0:
            detail = _sanitize_7zip_output(
                "\n".join(part for part in (completed.stderr, completed.stdout) if part)
            )
            message = f"7-Zip {action} failed with exit code {completed.returncode}"
            if detail:
                message = f"{message}: {detail}"
            raise ArchivePackagingError(message)


class ArchivePackager:
    def __init__(
        self,
        store: SQLiteClientStore,
        *,
        device_id: str,
        seven_zip_path: str | Path | None = None,
        artifact_manager: CacheArtifactManager | None = None,
    ) -> None:
        cleaned_device_id = device_id.strip()
        if not cleaned_device_id:
            raise ArchivePackagingError("device_id is required")
        self.store = store
        self.device_id = cleaned_device_id
        self.runner = SevenZipRunner(seven_zip_path)
        self.artifact_manager = artifact_manager

    def package_job(
        self,
        backup_job_id: str,
        *,
        cache_root: str | Path,
        password: str,
        archive_seq: int = 1,
        backup_source_id: str = "",
        source_seq: int | None = None,
        now: str | None = None,
    ) -> ArchivePackageResult:
        cleaned_job_id = backup_job_id.strip()
        if not cleaned_job_id:
            raise ArchivePackagingError("backup_job_id is required")
        if archive_seq < 1:
            raise ArchivePackagingError("archive_seq must be >= 1")
        if not password:
            raise ArchivePackagingError("archive password is required")

        actual_now = now or utc_now_iso()
        context = _load_job_manifest_context(self.store, cleaned_job_id)
        context = _filter_manifest_context(context, backup_source_id=backup_source_id, source_seq=source_seq)
        manifest_id = _manifest_id(cleaned_job_id, archive_seq)
        archive_id = _archive_id(cleaned_job_id, archive_seq, manifest_id)
        archive_type = _archive_type(context.file_references)
        external_references = _payload_archive_references(
            self.store,
            cleaned_job_id,
            exclude_archive_id=archive_id,
        )
        manifest_data = _build_manifest_data(
            context,
            device_id=self.device_id,
            manifest_id=manifest_id,
            archive_id=archive_id,
            created_at=actual_now,
            external_references=external_references,
        )
        _reject_secret_keys(manifest_data)
        manifest_text = stable_json_dumps(manifest_data)
        manifest_bytes = manifest_text.encode("utf-8")
        manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()

        job_cache = build_job_cache_dir(cache_root, cleaned_job_id)
        manifest_plain_dir = job_cache / "manifest_plain"
        staging_dir = job_cache / "tmp" / f"archive_{archive_seq:06d}"
        verify_dir = job_cache / "verify" / f"archive_{archive_seq:06d}"
        archives_dir = job_cache / "archives"
        temp_archive = archives_dir / f"{archive_seq:06d}-building.7z"

        _reset_dir(manifest_plain_dir)
        _reset_dir(staging_dir)
        _reset_dir(verify_dir)
        self._register_artifact(
            manifest_plain_dir,
            artifact_type="manifest_plain",
            job_id=cleaned_job_id,
            required_until_stage="verified",
            now=actual_now,
        )
        self._register_artifact(
            staging_dir,
            artifact_type="staging",
            job_id=cleaned_job_id,
            required_until_stage="verified",
            now=actual_now,
        )
        self._register_artifact(
            verify_dir,
            artifact_type="verify",
            job_id=cleaned_job_id,
            required_until_stage="strict_verified",
            now=actual_now,
        )
        local_fs.make_dirs(archives_dir)

        manifest_plain_path = manifest_plain_dir / "manifest.json"
        with local_fs.open_file(manifest_plain_path, "w", encoding="utf-8") as handle:
            handle.write(manifest_text)
        staging_manifest = staging_dir / "manifest" / "manifest.json"
        local_fs.make_dirs(staging_manifest.parent)
        with local_fs.open_file(staging_manifest, "w", encoding="utf-8") as handle:
            handle.write(manifest_text)

        payload_refs = _payload_references(context.file_references)
        payload_members = _stage_payload_members(payload_refs, staging_dir / "payload")

        if local_fs.exists(temp_archive):
            local_fs.unlink(temp_archive)
        try:
            self.runner.create_archive(archive_path=temp_archive, staging_dir=staging_dir, password=password)
            archive_sha256 = file_sha256(temp_archive)
            archive_md5 = file_md5(temp_archive)
            final_archive = archives_dir / f"{archive_seq:06d}-{archive_sha256}.7z"
            _replace_file(temp_archive, final_archive)
            self.runner.test_archive(archive_path=final_archive, password=password)
            extracted_manifest = self.runner.extract_manifest(
                archive_path=final_archive,
                output_dir=verify_dir,
                password=password,
            )
            if file_sha256(extracted_manifest) != manifest_sha256:
                raise ArchivePackagingError("archive manifest hash mismatch after extraction")
        except Exception:
            if local_fs.exists(temp_archive):
                local_fs.unlink(temp_archive)
            raise
        finally:
            _remove_dir(manifest_plain_dir)
            _remove_dir(staging_dir)
            _remove_dir(verify_dir)
            self._mark_artifact_missing(manifest_plain_dir, now=actual_now)
            self._mark_artifact_missing(staging_dir, now=actual_now)
            self._mark_artifact_missing(verify_dir, now=actual_now)

        archive_size = _file_size(final_archive)
        self._register_artifact(
            final_archive,
            artifact_type="archive",
            job_id=cleaned_job_id,
            required_until_stage="completed",
            now=actual_now,
        )
        result = ArchivePackageResult(
            backup_job_id=cleaned_job_id,
            archive_id=archive_id,
            archive_seq=archive_seq,
            archive_path=final_archive,
            archive_sha256=archive_sha256,
            archive_md5=archive_md5,
            archive_size=archive_size,
            archive_type=archive_type,
            manifest_id=manifest_id,
            manifest_sha256=manifest_sha256,
            manifest_size=len(manifest_bytes),
            manifest_item_count=len(manifest_data["items"]),
            payload_member_count=len(payload_members),
            reference_member_count=len(context.file_references) - len(payload_members),
        )
        self._write_archive_state(
            result,
            context=context,
            payload_members=payload_members,
            external_references=external_references,
            now=actual_now,
        )
        return result

    def _register_artifact(
        self,
        path: Path,
        *,
        artifact_type: str,
        job_id: str,
        required_until_stage: str,
        now: str,
    ) -> None:
        if self.artifact_manager is None:
            return
        self.artifact_manager.register_path(
            path=path,
            artifact_type=artifact_type,
            job_id=job_id,
            required_until_stage=required_until_stage,
            now=now,
        )

    def _mark_artifact_missing(self, path: Path, *, now: str) -> None:
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

    def _write_archive_state(
        self,
        result: ArchivePackageResult,
        *,
        context: "_ManifestContext",
        payload_members: Mapping[str, str],
        external_references: Mapping[str, Mapping[str, str]],
        now: str,
    ) -> None:
        with self.store.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM archives WHERE archive_id = ?",
                (result.archive_id,),
            ).fetchone()
            archive_payload = build_version_fields(
                entity_payload={
                    "archive_id": result.archive_id,
                    "entity_id": f"archive_{result.archive_id}",
                    "job_id": result.backup_job_id,
                    "device_id": self.device_id,
                    "archive_seq": result.archive_seq,
                    "archive_sha256": result.archive_sha256,
                    "archive_md5": result.archive_md5,
                    "archive_size": result.archive_size,
                    "archive_type": result.archive_type,
                    "manifest_id": result.manifest_id,
                    "manifest_sha256": result.manifest_sha256,
                    "manifest_size": result.manifest_size,
                    "manifest_item_count": result.manifest_item_count,
                    "payload_member_count": result.payload_member_count,
                    "reference_member_count": result.reference_member_count,
                    "local_archive_path": str(result.archive_path),
                    "remote_path": str(existing["remote_path"]) if existing is not None else "",
                    "verify_status": "standard_test_passed",
                    "standard_verified_at": now,
                    "strict_verify_status": "not_requested",
                    "created_at": str(existing["created_at"]) if existing is not None else now,
                },
                updated_by_device_id=self.device_id,
                data_version=int(existing["data_version"]) + 1 if existing is not None else 1,
                schema_version=int(existing["schema_version"]) if existing is not None else 1,
                now=now,
                sync_status="sync_pending",
                deleted_at=existing["deleted_at"] if existing is not None else None,
                last_synced_revision_id=existing["last_synced_revision_id"] if existing is not None else None,
            )
            self.store.put_archive(conn, archive_payload)
            conn.execute("DELETE FROM archive_members WHERE archive_id = ?", (result.archive_id,))
            self.store.put_archive_member(
                conn,
                _archive_member_payload(
                    device_id=self.device_id,
                    archive_id=result.archive_id,
                    job_id=result.backup_job_id,
                    member_type="manifest",
                    member_path="manifest/manifest.json",
                    created_at=now,
                ),
            )
            for content_id, member_path in sorted(payload_members.items()):
                ref = next(row for row in context.file_references if row["content_id"] == content_id)
                self.store.put_archive_member(
                    conn,
                    _archive_member_payload(
                        device_id=self.device_id,
                        archive_id=result.archive_id,
                        job_id=result.backup_job_id,
                        member_type="payload",
                        member_path=member_path,
                        content_reference_id=str(ref["content_reference_id"]),
                        file_item_id=str(ref["file_item_id"]),
                        content_id=content_id,
                        file_sha256=str(ref["file_sha256"]),
                        size_bytes=int(ref["size_bytes"]),
                        created_at=now,
                    ),
                )
            for ref in context.file_references:
                content_id = str(ref["content_id"])
                member_path = payload_members.get(content_id, "")
                if not member_path and str(ref["reference_role"]) == "local_duplicate":
                    member_path = _payload_member_path(content_id)
                external = external_references.get(content_id, {})
                if str(ref["reference_role"]) in {"local_duplicate", "cloud_duplicate_candidate"}:
                    self.store.put_archive_member(
                        conn,
                        _archive_member_payload(
                            device_id=self.device_id,
                            archive_id=result.archive_id,
                            job_id=result.backup_job_id,
                            member_type="reference",
                            member_path=member_path or "manifest/manifest.json",
                            content_reference_id=str(ref["content_reference_id"]),
                            file_item_id=str(ref["file_item_id"]),
                            content_id=content_id,
                            file_sha256=str(ref["file_sha256"]),
                            size_bytes=int(ref["size_bytes"]),
                            referenced_archive_id=str(external.get("archive_id", "")),
                            referenced_archive_remote_path=str(external.get("remote_path", "")),
                            created_at=now,
                        ),
                    )
                next_status = (
                    "cloud_duplicate_candidate"
                    if str(ref["reference_role"]) == "cloud_duplicate_candidate"
                    else "archive_assigned"
                )
                self.store.update_content_reference_archive(
                    conn,
                    content_reference_id=str(ref["content_reference_id"]),
                    archive_id=result.archive_id,
                    archive_sha256=result.archive_sha256,
                    archive_member_path=member_path,
                    dedupe_status=next_status,
                    updated_at=now,
                    updated_by_device_id=self.device_id,
                )
            for folder in context.folders:
                self.store.put_archive_member(
                    conn,
                    _archive_member_payload(
                        device_id=self.device_id,
                        archive_id=result.archive_id,
                        job_id=result.backup_job_id,
                        member_type="folder",
                        member_path=str(folder["relative_path"]),
                        folder_item_id=str(folder["folder_item_id"]),
                        created_at=now,
                    ),
                )


@dataclass(frozen=True)
class _ManifestContext:
    job: dict[str, Any]
    sources: tuple[dict[str, Any], ...]
    file_references: tuple[dict[str, Any], ...]
    folders: tuple[dict[str, Any], ...]


def resolve_7zip_executable(candidate: str | Path | None = None) -> Path:
    candidates: list[Path] = []
    if candidate:
        candidates.append(Path(candidate))
    env_path = os.environ.get("AUTO_BACKUP_7ZIP_PATH", "").strip()
    if env_path:
        candidates.append(Path(env_path))
    resolved_from_path = shutil.which("7z")
    if resolved_from_path:
        candidates.append(Path(resolved_from_path))
    candidates.extend(DEFAULT_SEVEN_ZIP_CANDIDATES)
    for item in candidates:
        if item.is_file():
            return item
    raise SevenZipNotFoundError("7-Zip executable was not found")


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with local_fs.open_file(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_md5(path: str | Path) -> str:
    digest = hashlib.md5()
    with local_fs.open_file(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_job_manifest_context(store: SQLiteClientStore, backup_job_id: str) -> _ManifestContext:
    with store.connect() as conn:
        job = conn.execute("SELECT * FROM backup_jobs WHERE backup_job_id = ?", (backup_job_id,)).fetchone()
        if job is None:
            raise ArchivePackagingError("backup job not found")
        sources = conn.execute(
            """
            SELECT *
            FROM backup_sources
            WHERE backup_job_id = ?
            ORDER BY source_seq, backup_source_id
            """,
            (backup_job_id,),
        ).fetchall()
        references = conn.execute(
            """
            SELECT
                cr.*,
                fi.ctime_ns,
                fi.mtime_ns,
                fi.atime_ns,
                fi.file_attrs,
                fi.quick_fingerprint,
                fi.quick_sample_count,
                fi.quick_sample_size,
                fi.sample_plan_json,
                fi.scan_status
            FROM content_references cr
            JOIN file_items fi ON fi.file_item_id = cr.file_item_id
            WHERE cr.backup_job_id = ?
            ORDER BY cr.source_seq, cr.relative_path, cr.content_reference_id
            """,
            (backup_job_id,),
        ).fetchall()
        folders = conn.execute(
            """
            SELECT *
            FROM folder_items
            WHERE backup_job_id = ?
            ORDER BY source_seq, relative_path, folder_item_id
            """,
            (backup_job_id,),
        ).fetchall()
    if not references and not folders:
        raise ArchivePackagingError("backup job has no indexed files or folders to package")
    return _ManifestContext(
        job=dict(job),
        sources=tuple(dict(row) for row in sources),
        file_references=tuple(dict(row) for row in references),
        folders=tuple(dict(row) for row in folders),
    )


def _filter_manifest_context(
    context: _ManifestContext,
    *,
    backup_source_id: str = "",
    source_seq: int | None = None,
) -> _ManifestContext:
    cleaned_source_id = backup_source_id.strip()
    if not cleaned_source_id and source_seq is None:
        return context
    if cleaned_source_id:
        sources = tuple(source for source in context.sources if str(source["backup_source_id"]) == cleaned_source_id)
    else:
        sources = tuple(source for source in context.sources if int(source["source_seq"]) == int(source_seq or 0))
    if len(sources) != 1:
        raise ArchivePackagingError("backup source not found for archive packaging")
    selected_source_id = str(sources[0]["backup_source_id"])
    file_references = tuple(ref for ref in context.file_references if str(ref["backup_source_id"]) == selected_source_id)
    folders = tuple(folder for folder in context.folders if str(folder["backup_source_id"]) == selected_source_id)
    if not file_references and not folders:
        raise ArchivePackagingError("backup source has no indexed files or folders to package")
    return _ManifestContext(
        job=context.job,
        sources=sources,
        file_references=file_references,
        folders=folders,
    )


def _payload_archive_references(
    store: SQLiteClientStore,
    backup_job_id: str,
    *,
    exclude_archive_id: str = "",
) -> dict[str, dict[str, str]]:
    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT
                am.content_id,
                am.archive_id,
                am.member_path,
                ro.remote_path
            FROM archive_members am
            LEFT JOIN remote_objects ro
                ON ro.archive_id = am.archive_id
               AND ro.object_type = 'archive'
            WHERE am.job_id = ?
              AND am.member_type = 'payload'
              AND am.archive_id <> ?
            ORDER BY am.created_at DESC, am.archive_member_id
            """,
            (backup_job_id, exclude_archive_id),
        ).fetchall()
    references: dict[str, dict[str, str]] = {}
    for row in rows:
        content_id = str(row["content_id"])
        references.setdefault(
            content_id,
            {
                "archive_id": str(row["archive_id"] or ""),
                "member_path": str(row["member_path"] or ""),
                "remote_path": str(row["remote_path"] or ""),
            },
        )
    return references


def _build_manifest_data(
    context: _ManifestContext,
    *,
    device_id: str,
    manifest_id: str,
    archive_id: str,
    created_at: str,
    external_references: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    external_references = external_references or {}
    items: list[dict[str, Any]] = []
    for folder in context.folders:
        items.append(
            {
                "item_id": str(folder["folder_item_id"]),
                "item_type": "folder",
                "backup_source_id": str(folder["backup_source_id"]),
                "source_seq": int(folder["source_seq"]),
                "original_name": str(folder["display_name"]),
                "relative_path": str(folder["relative_path"]),
                "ctime_ns": int(folder["ctime_ns"]),
                "mtime_ns": int(folder["mtime_ns"]),
                "atime_ns": int(folder["atime_ns"]),
                "windows_attributes": int(folder["file_attrs"]),
                "folder_content_hash": str(folder["folder_content_hash"]),
                "folder_manifest_hash": str(folder["folder_manifest_hash"]),
                "archive_id": archive_id,
                "archive_member_path": "manifest/manifest.json",
            }
        )
    for ref in context.file_references:
        content_id = str(ref["content_id"])
        is_payload = _is_payload_reference(ref)
        member_path = _payload_member_path(content_id) if is_payload or str(ref["reference_role"]) == "local_duplicate" else ""
        external = external_references.get(content_id, {})
        items.append(
            {
                "item_id": str(ref["file_item_id"]),
                "item_type": "file",
                "content_reference_id": str(ref["content_reference_id"]),
                "backup_source_id": str(ref["backup_source_id"]),
                "source_seq": int(ref["source_seq"]),
                "original_name": str(ref["display_name"]),
                "original_path": str(ref["local_path"]),
                "relative_path": str(ref["relative_path"]),
                "size": int(ref["size_bytes"]),
                "ctime_ns": int(ref["ctime_ns"]),
                "mtime_ns": int(ref["mtime_ns"]),
                "atime_ns": int(ref["atime_ns"]),
                "windows_attributes": int(ref["file_attrs"]),
                "quick_fingerprint": str(ref["quick_fingerprint"]),
                "quick_sample_count": int(ref["quick_sample_count"]),
                "quick_sample_size": int(ref["quick_sample_size"]),
                "md5": str(ref["md5"]),
                "sha256": str(ref["file_sha256"]),
                "content_id": content_id,
                "duplicate_status": str(ref["dedupe_status"]),
                "duplicate_of_content_id": content_id if not is_payload else "",
                "archive_id": archive_id,
                "archive_member_path": member_path,
                "referenced_archive_id": str(external.get("archive_id", "")) if not is_payload else "",
                "referenced_archive_remote_path": str(external.get("remote_path", "")) if not is_payload else "",
            }
        )
    return {
        "manifest_version": MANIFEST_VERSION,
        "manifest_id": manifest_id,
        "job_id": str(context.job["backup_job_id"]),
        "job_name": str(context.job["job_name"]),
        "created_at": created_at,
        "device_id": device_id,
        "hostname": "",
        "os_version": "",
        "source_roots": [
            {
                "backup_source_id": str(source["backup_source_id"]),
                "source_seq": int(source["source_seq"]),
                "source_type": str(source["source_type"]),
                "original_path": str(source["local_path"]),
                "display_name": str(source["display_name"]),
                "path_sha256": str(source["path_sha256"]),
            }
            for source in context.sources
        ],
        "items": sorted(items, key=lambda item: (int(item["source_seq"]), str(item["relative_path"]), str(item["item_id"]))),
    }


def _payload_references(file_references: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    seen: set[str] = set()
    payload_refs: list[Mapping[str, Any]] = []
    for ref in file_references:
        if not _is_payload_reference(ref):
            continue
        content_id = str(ref["content_id"])
        if content_id in seen:
            continue
        seen.add(content_id)
        payload_refs.append(ref)
    return tuple(payload_refs)


def _stage_payload_members(payload_refs: Sequence[Mapping[str, Any]], payload_dir: Path) -> dict[str, str]:
    members: dict[str, str] = {}
    for ref in payload_refs:
        source = Path(str(ref["local_path"]))
        if not local_fs.is_file(source):
            raise ArchivePackagingError("payload source file is missing")
        expected_size = int(ref["size_bytes"])
        if _file_size(source) != expected_size or file_sha256(source) != str(ref["file_sha256"]):
            raise PayloadSourceChangedError(
                file_item_id=str(ref["file_item_id"]),
                display_name=str(ref["display_name"]),
            )
        content_id = str(ref["content_id"])
        local_fs.make_dirs(payload_dir)
        target = payload_dir / content_id
        if not local_fs.exists(target):
            with local_fs.open_file(source, "rb") as src, local_fs.open_file(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
        members[content_id] = _payload_member_path(content_id)
    return members


def _archive_member_payload(
    *,
    device_id: str,
    archive_id: str,
    job_id: str,
    member_type: str,
    member_path: str,
    created_at: str,
    content_reference_id: str = "",
    file_item_id: str = "",
    folder_item_id: str = "",
    content_id: str = "",
    file_sha256: str = "",
    size_bytes: int = 0,
    referenced_archive_id: str = "",
    referenced_archive_remote_path: str = "",
) -> dict[str, Any]:
    digest = hashlib.sha256(
        f"{archive_id}\0{member_type}\0{member_path}\0{content_reference_id}\0{file_item_id}\0{folder_item_id}".encode("utf-8")
    ).hexdigest()
    archive_member_id = f"amem_{digest}"
    return build_version_fields(
        entity_payload={
            "archive_member_id": archive_member_id,
            "entity_id": f"archive_member_{archive_member_id}",
            "archive_id": archive_id,
            "job_id": job_id,
            "content_reference_id": content_reference_id,
            "file_item_id": file_item_id,
            "folder_item_id": folder_item_id,
            "content_id": content_id,
            "member_type": member_type,
            "member_path": member_path,
            "file_sha256": file_sha256,
            "size_bytes": int(size_bytes),
            "referenced_archive_id": referenced_archive_id,
            "referenced_archive_remote_path": referenced_archive_remote_path,
            "created_at": created_at,
        },
        updated_by_device_id=device_id,
        now=created_at,
        sync_status="sync_pending",
    )


def _archive_id(job_id: str, archive_seq: int, manifest_id: str) -> str:
    digest = hashlib.sha256(f"{job_id}\0{archive_seq}\0{manifest_id}".encode("utf-8")).hexdigest()
    return f"archive_{digest}"


def _manifest_id(job_id: str, archive_seq: int) -> str:
    digest = hashlib.sha256(f"{job_id}\0{archive_seq}\0manifest".encode("utf-8")).hexdigest()
    return f"manifest_{digest}"


def _payload_member_path(content_id: str) -> str:
    return f"payload/{content_id}"


def _archive_type(file_references: Sequence[Mapping[str, Any]]) -> str:
    payload_count = sum(1 for ref in file_references if _is_payload_reference(ref))
    if payload_count == 0:
        return "manifest_only"
    if payload_count == len(file_references):
        return "payload"
    return "mixed"


def _is_payload_reference(ref: Mapping[str, Any]) -> bool:
    return str(ref["reference_role"]) == "payload_source" and str(ref["dedupe_status"]) == "needs_payload"


def _reject_secret_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered = str(key).lower()
            if any(hint in lowered for hint in SECRET_KEY_HINTS):
                raise ArchivePackagingError(f"manifest contains sensitive field: {key}")
            _reject_secret_keys(item)
    elif isinstance(value, list):
        for item in value:
            _reject_secret_keys(item)


def _sanitize_7zip_output(value: str) -> str:
    text = value.replace("\r", " ").replace("\n", " ")
    redacted_parts: list[str] = []
    for raw_part in text.split():
        part = raw_part.strip()
        if not part:
            continue
        if _looks_sensitive_7zip_token(part):
            redacted_parts.append("[redacted-path]")
        else:
            redacted_parts.append(part)
    sanitized = " ".join(redacted_parts)
    if len(sanitized) > 360:
        sanitized = sanitized[:357] + "..."
    return sanitized


def _looks_sensitive_7zip_token(value: str) -> bool:
    stripped = value.strip("\"'")
    if stripped.startswith("-p") and len(stripped) > 2:
        return True
    if stripped.startswith("-o") and (":\\" in stripped or ":/" in stripped or stripped.startswith("-o\\\\")):
        return True
    if ":\\" in stripped or ":/" in stripped or stripped.startswith("\\\\"):
        return True
    return False


def _reset_dir(path: Path) -> None:
    _remove_dir(path)
    local_fs.make_dirs(path)


def _remove_dir(path: Path) -> None:
    local_fs.remove_tree(path)


def _replace_file(source: Path, target: Path) -> None:
    local_fs.replace_file(source, target)


def _file_size(path: Path) -> int:
    return local_fs.file_size(path)
