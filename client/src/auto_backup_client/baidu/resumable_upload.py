from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from auto_backup_client import local_fs
from auto_backup_client.cache_artifacts import CacheArtifactManager
from auto_backup_client.baidu.metadata import (
    ArchiveMetaInput,
    JobIndexArchive,
    StableJsonDocument,
    build_archive_meta_document,
    build_job_index_document,
)
from auto_backup_client.baidu.upload import (
    DEFAULT_BACKUP_ROOT_DIR,
    DEFAULT_PART_SIZE,
    BaiduNetdiskClient,
    BaiduNetdiskError,
    CreateFileResult,
    FileBlockPlan,
    PrecreateResult,
    build_archive_remote_path,
    compute_file_block_plan,
)
from auto_backup_client.sqlite_store import (
    SQLiteClientStore,
    build_version_fields,
    utc_now_iso,
)


UPLOADID_EXPIRED_CODES = frozenset({"2", "31190", "31363", "invalid_uploadid", "uploadid_expired"})


@dataclass(frozen=True)
class ResumableArchiveInput:
    local_path: Path
    job_id: str
    device_id: str
    account_id: str
    archive_id: str = ""
    archive_seq: int = 1
    archive_type: str = "payload"
    manifest_id: str = ""
    root_dir: str = DEFAULT_BACKUP_ROOT_DIR
    job_created_at: datetime | None = None
    part_size: int = DEFAULT_PART_SIZE


@dataclass(frozen=True)
class ResumableUploadResult:
    upload_session_id: str
    archive_id: str
    archive_sha256: str
    remote_archive_path: str
    remote_meta_path: str
    remote_job_index_path: str
    uploadid: str
    reused_uploadid: bool
    uploaded_partseqs: tuple[int, ...]
    created: CreateFileResult
    meta_created: CreateFileResult
    job_index_created: CreateFileResult
    archive_meta: StableJsonDocument
    job_index: StableJsonDocument


class BaiduResumableUploader:
    def __init__(
        self,
        *,
        store: SQLiteClientStore,
        baidu: BaiduNetdiskClient,
        updated_by_device_id: str,
        artifact_manager: CacheArtifactManager | None = None,
    ) -> None:
        self._store = store
        self._baidu = baidu
        self._updated_by_device_id = updated_by_device_id
        self._artifact_manager = artifact_manager

    def upload(self, value: ResumableArchiveInput) -> ResumableUploadResult:
        plan = compute_file_block_plan(value.local_path, part_size=value.part_size)
        archive_sha256 = file_sha256(value.local_path)
        archive_id = value.archive_id.strip() or f"archive_{archive_sha256}"
        job_created_at = value.job_created_at or datetime.now(timezone.utc)
        upload_session_id = _session_id(value.job_id, archive_sha256, value.archive_seq)
        stored = self._store.get_upload_session(upload_session_id)
        if stored:
            remote_archive_path = str(stored["remote_archive_path"])
            remote_meta_path = str(stored["remote_meta_path"])
            remote_job_index_path = str(stored["remote_job_index_path"])
            completed = self._completed_from_store(
                stored=stored,
                remote_archive_path=remote_archive_path,
                remote_meta_path=remote_meta_path,
                remote_job_index_path=remote_job_index_path,
                archive_sha256=archive_sha256,
                job_created_at=job_created_at,
                value=value,
                plan=plan,
            )
            if completed is not None:
                return completed
        else:
            remote_archive_path = build_archive_remote_path(
                root_dir=value.root_dir,
                job_created_at=job_created_at,
                device_id=value.device_id,
                job_id=value.job_id,
                archive_seq=value.archive_seq,
                archive_sha256=archive_sha256,
                suffix=".7z",
            )
            remote_meta_path = build_archive_remote_path(
                root_dir=value.root_dir,
                job_created_at=job_created_at,
                device_id=value.device_id,
                job_id=value.job_id,
                archive_seq=value.archive_seq,
                archive_sha256=archive_sha256,
                suffix=".meta.json",
            )
            remote_job_index_path = remote_archive_path.rsplit("/archives/", 1)[0] + "/job.index.json"
        uploadid = str(stored.get("uploadid", "")) if stored else ""

        session_base = _session_payload(
            upload_session_id=upload_session_id,
            job_id=value.job_id,
            device_id=value.device_id,
            account_id=value.account_id,
            archive_id=archive_id,
            archive_seq=value.archive_seq,
            archive_sha256=archive_sha256,
            archive_md5=plan.content_md5,
            archive_size=plan.size,
            archive_type=value.archive_type,
            local_archive_path=str(value.local_path),
            remote_archive_path=remote_archive_path,
            remote_meta_path=remote_meta_path,
            remote_job_index_path=remote_job_index_path,
            part_size=plan.part_size,
            total_parts=len(plan.parts),
            block_md5s=plan.block_md5s,
            uploadid=uploadid,
            upload_status="planned",
            fs_id=0,
            remote_md5="",
            error_code="",
            error_message="",
        )
        self._write_session_and_parts(session_base, plan)

        precreate, reused_uploadid = self._precreate_with_resume(remote_path=remote_archive_path, plan=plan, uploadid=uploadid)
        precreated_payload = {
            **session_base,
            "uploadid": precreate.uploadid,
            "upload_status": "precreated",
            "error_code": "",
            "error_message": "",
        }
        self._write_session(precreated_payload)

        uploaded_partseqs: list[int] = []
        try:
            locate = self._baidu.locate_upload_server(remote_path=remote_archive_path, uploadid=precreate.uploadid)
            required_partseqs = set(_missing_partseqs(precreate, total_parts=len(plan.parts)))
            for part in plan.parts:
                if part.partseq not in required_partseqs:
                    self._write_part_status(upload_session_id, plan, part.partseq, "confirmed")
            for partseq in _missing_partseqs(precreate, total_parts=len(plan.parts)):
                self._write_part_status(upload_session_id, plan, partseq, "uploading")
                part = self._baidu.upload_part(
                    upload_server=locate.upload_server,
                    remote_path=remote_archive_path,
                    uploadid=precreate.uploadid,
                    plan=plan,
                    partseq=partseq,
                )
                uploaded_partseqs.append(part.partseq)
                self._write_part_status(upload_session_id, plan, partseq, "uploaded")

            created = self._baidu.create_file(
                remote_path=remote_archive_path,
                size=plan.size,
                block_md5s=plan.block_md5s,
                uploadid=precreate.uploadid,
                rtype=0,
                local_ctime=_file_mtime_seconds(plan.file_path),
                local_mtime=_file_mtime_seconds(plan.file_path),
            )
            for part in plan.parts:
                self._write_part_status(upload_session_id, plan, part.partseq, "confirmed")
        except BaiduNetdiskError as exc:
            self._write_session(
                {
                    **precreated_payload,
                    "upload_status": "failed_retryable",
                    "error_code": exc.error_code,
                    "error_message": str(exc),
                }
            )
            raise
        archive_meta = build_archive_meta_document(
            ArchiveMetaInput(
                archive_id=archive_id,
                archive_seq=value.archive_seq,
                archive_sha256=archive_sha256,
                archive_md5=plan.content_md5,
                archive_size=plan.size,
                archive_type=value.archive_type,
                job_id=value.job_id,
                device_id=value.device_id,
                manifest_id=value.manifest_id or f"manifest_{value.job_id}",
                created_at=job_created_at,
            )
        )
        try:
            meta_created = self._upload_bytes_document(
                document=archive_meta,
                remote_path=remote_meta_path,
                work_name=f"{archive_id}.meta.json",
            )
            job_index = self._build_job_index_document(
                job_id=value.job_id,
                device_id=value.device_id,
                job_created_at=job_created_at,
                root_dir=value.root_dir,
                current=JobIndexArchive(
                    archive_id=archive_id,
                    archive_seq=value.archive_seq,
                    archive_sha256=archive_sha256,
                    archive_size=plan.size,
                    archive_type=value.archive_type,
                    remote_archive_path=remote_archive_path,
                    remote_meta_path=remote_meta_path,
                    fs_id=created.fs_id,
                    meta_sha256=archive_meta.sha256,
                ),
            )
            self._replace_existing_remote_document(remote_job_index_path)
            job_index_created = self._upload_bytes_document(
                document=job_index,
                remote_path=remote_job_index_path,
                work_name=f"{value.job_id}.job.index.json",
            )
        except BaiduNetdiskError as exc:
            self._write_session(
                {
                    **precreated_payload,
                    "upload_status": "remote_created",
                    "meta_status": "failed_retryable",
                    "job_index_status": "failed_retryable",
                    "fs_id": created.fs_id,
                    "remote_md5": created.md5,
                    "error_code": exc.error_code,
                    "error_message": str(exc),
                }
            )
            raise

        completed_payload = {
            **precreated_payload,
            "upload_status": "remote_created",
            "meta_status": "uploaded",
            "job_index_status": "uploaded",
            "fs_id": created.fs_id,
            "remote_md5": created.md5,
            "completed_at": utc_now_iso(),
        }
        self._write_session(completed_payload)
        self._write_remote_objects(
            job_id=value.job_id,
            device_id=value.device_id,
            archive_id=archive_id,
            archive_sha256=archive_sha256,
            archive_path=remote_archive_path,
            archive_size=plan.size,
            archive_md5=created.md5,
            archive_fs_id=created.fs_id,
            meta_path=remote_meta_path,
            meta_document=archive_meta,
            meta_md5=meta_created.md5,
            meta_fs_id=meta_created.fs_id,
            job_index_path=remote_job_index_path,
            job_index=job_index,
            job_index_md5=job_index_created.md5,
            job_index_fs_id=job_index_created.fs_id,
        )
        return ResumableUploadResult(
            upload_session_id=upload_session_id,
            archive_id=archive_id,
            archive_sha256=archive_sha256,
            remote_archive_path=remote_archive_path,
            remote_meta_path=remote_meta_path,
            remote_job_index_path=remote_job_index_path,
            uploadid=precreate.uploadid,
            reused_uploadid=reused_uploadid,
            uploaded_partseqs=tuple(uploaded_partseqs),
            created=created,
            meta_created=meta_created,
            job_index_created=job_index_created,
            archive_meta=archive_meta,
            job_index=job_index,
        )

    def _completed_from_store(
        self,
        *,
        stored: dict[str, Any],
        remote_archive_path: str,
        remote_meta_path: str,
        remote_job_index_path: str,
        archive_sha256: str,
        job_created_at: datetime,
        value: ResumableArchiveInput,
        plan: FileBlockPlan,
    ) -> ResumableUploadResult | None:
        if (
            stored.get("upload_status") != "remote_created"
            or stored.get("meta_status") != "uploaded"
            or stored.get("job_index_status") != "uploaded"
        ):
            return None
        archive_object = self._store.get_remote_object_by_path(remote_archive_path)
        meta_object = self._store.get_remote_object_by_path(remote_meta_path)
        job_index_object = self._store.get_remote_object_by_path(remote_job_index_path)
        if archive_object is None or meta_object is None or job_index_object is None:
            return None

        archive_id = str(stored["archive_id"])
        archive_meta = build_archive_meta_document(
            ArchiveMetaInput(
                archive_id=archive_id,
                archive_seq=value.archive_seq,
                archive_sha256=archive_sha256,
                archive_md5=plan.content_md5,
                archive_size=plan.size,
                archive_type=value.archive_type,
                job_id=value.job_id,
                device_id=value.device_id,
                manifest_id=value.manifest_id or f"manifest_{value.job_id}",
                created_at=job_created_at,
            )
        )
        job_index = build_job_index_document(
            job_id=value.job_id,
            device_id=value.device_id,
            job_created_at=job_created_at,
            root_dir=value.root_dir,
            archives=(
                JobIndexArchive(
                    archive_id=archive_id,
                    archive_seq=value.archive_seq,
                    archive_sha256=archive_sha256,
                    archive_size=plan.size,
                    archive_type=value.archive_type,
                    remote_archive_path=remote_archive_path,
                    remote_meta_path=remote_meta_path,
                    fs_id=int(archive_object["fs_id"] or 0),
                    meta_sha256=str(meta_object["sha256"] or archive_meta.sha256),
                ),
            ),
        )
        return ResumableUploadResult(
            upload_session_id=str(stored["upload_session_id"]),
            archive_id=archive_id,
            archive_sha256=archive_sha256,
            remote_archive_path=remote_archive_path,
            remote_meta_path=remote_meta_path,
            remote_job_index_path=remote_job_index_path,
            uploadid=str(stored.get("uploadid", "")),
            reused_uploadid=False,
            uploaded_partseqs=tuple(),
            created=CreateFileResult(
                fs_id=int(archive_object["fs_id"] or 0),
                path=remote_archive_path,
                md5=str(archive_object["md5"] or stored.get("remote_md5", "")),
                server_filename=remote_archive_path.rsplit("/", 1)[-1],
            ),
            meta_created=CreateFileResult(
                fs_id=int(meta_object["fs_id"] or 0),
                path=remote_meta_path,
                md5=str(meta_object["md5"] or ""),
                server_filename=remote_meta_path.rsplit("/", 1)[-1],
            ),
            job_index_created=CreateFileResult(
                fs_id=int(job_index_object["fs_id"] or 0),
                path=remote_job_index_path,
                md5=str(job_index_object["md5"] or ""),
                server_filename=remote_job_index_path.rsplit("/", 1)[-1],
            ),
            archive_meta=archive_meta,
            job_index=job_index,
        )

    def _precreate_with_resume(self, *, remote_path: str, plan: FileBlockPlan, uploadid: str) -> tuple[PrecreateResult, bool]:
        timestamp = _file_mtime_seconds(plan.file_path)
        if uploadid:
            try:
                return (
                    self._baidu.precreate(
                        remote_path=remote_path,
                        size=plan.size,
                        block_md5s=plan.block_md5s,
                        content_md5=plan.content_md5,
                        slice_md5=plan.slice_md5,
                        uploadid=uploadid,
                        rtype=0,
                        local_ctime=timestamp,
                        local_mtime=timestamp,
                    ),
                    True,
                )
            except BaiduNetdiskError as exc:
                if exc.error_code not in UPLOADID_EXPIRED_CODES:
                    raise
        return (
            self._baidu.precreate(
                remote_path=remote_path,
                size=plan.size,
                block_md5s=plan.block_md5s,
                content_md5=plan.content_md5,
                slice_md5=plan.slice_md5,
                rtype=0,
                local_ctime=timestamp,
                local_mtime=timestamp,
            ),
            False,
        )

    def _upload_bytes_document(self, *, document: StableJsonDocument, remote_path: str, work_name: str) -> CreateFileResult:
        temp_parent = None
        if self._artifact_manager is not None:
            temp_parent = self._artifact_manager.cache_root / "upload_tmp"
            local_fs.make_dirs(temp_parent)
        with tempfile.TemporaryDirectory(prefix="auto-backup-upload-meta-", dir=str(temp_parent) if temp_parent is not None else None) as temp_dir:
            path = Path(temp_dir) / work_name
            path.write_bytes(document.bytes)
            if self._artifact_manager is not None:
                self._artifact_manager.register_path(
                    path=path,
                    artifact_type="upload_temp",
                    required_until_stage="uploaded",
                )
            try:
                return self._baidu.upload_file_complete(
                    local_path=path,
                    remote_path=remote_path,
                    part_size=DEFAULT_PART_SIZE,
                    rtype=0,
                ).created
            finally:
                if self._artifact_manager is not None:
                    artifact_id = f"artifact_{hashlib.sha256(str(path.resolve()).encode('utf-8')).hexdigest()}"
                    with self._store.transaction() as conn:
                        self._store.update_cache_artifact_status(
                            conn,
                            artifact_id=artifact_id,
                            lifecycle_status="deleted",
                            size_bytes=0,
                            deleted_at=utc_now_iso(),
                            last_accessed_at=utc_now_iso(),
                        )

    def _replace_existing_remote_document(self, remote_path: str) -> None:
        if self._store.get_remote_object_by_path(remote_path) is None:
            return
        result = self._baidu.delete_files((remote_path,), async_mode=0)
        if result.errno != 0:
            raise BaiduNetdiskError("baidu delete existing metadata failed", error_code=str(result.errno))

    def _build_job_index_document(
        self,
        *,
        job_id: str,
        device_id: str,
        job_created_at: datetime,
        root_dir: str,
        current: JobIndexArchive,
    ) -> StableJsonDocument:
        archives_by_id: dict[str, JobIndexArchive] = {}
        for row in self._store.list_archives(job_id, limit=5000):
            archive_id = str(row["archive_id"])
            if archive_id == current.archive_id:
                continue
            remote = self._remote_paths_for_archive(job_id=job_id, archive_id=archive_id)
            if not remote:
                continue
            archives_by_id[archive_id] = JobIndexArchive(
                archive_id=archive_id,
                archive_seq=int(row["archive_seq"] or 0),
                archive_sha256=str(row["archive_sha256"] or ""),
                archive_size=int(row["archive_size"] or 0),
                archive_type=str(row["archive_type"] or ""),
                remote_archive_path=str(remote.get("archive", {}).get("remote_path", "")),
                remote_meta_path=str(remote.get("archive_meta", {}).get("remote_path", "")),
                fs_id=int(remote.get("archive", {}).get("fs_id") or 0),
                meta_sha256=str(remote.get("archive_meta", {}).get("sha256") or ""),
            )
        for row in self._store.list_upload_sessions_for_reconcile(job_id=job_id):
            archive_id = str(row["archive_id"])
            if not archive_id or archive_id == current.archive_id or archive_id in archives_by_id:
                continue
            if str(row.get("upload_status", "")) != "remote_created" or str(row.get("meta_status", "")) != "uploaded":
                continue
            remote = self._remote_paths_for_archive(job_id=job_id, archive_id=archive_id)
            if not remote:
                continue
            archives_by_id[archive_id] = JobIndexArchive(
                archive_id=archive_id,
                archive_seq=int(row["archive_seq"] or 0),
                archive_sha256=str(row["archive_sha256"] or ""),
                archive_size=int(row["archive_size"] or 0),
                archive_type=str(row["archive_type"] or ""),
                remote_archive_path=str(row["remote_archive_path"] or remote.get("archive", {}).get("remote_path", "")),
                remote_meta_path=str(row["remote_meta_path"] or remote.get("archive_meta", {}).get("remote_path", "")),
                fs_id=int(remote.get("archive", {}).get("fs_id") or row.get("fs_id") or 0),
                meta_sha256=str(remote.get("archive_meta", {}).get("sha256") or ""),
            )
        archives_by_id[current.archive_id] = current
        return build_job_index_document(
            job_id=job_id,
            device_id=device_id,
            job_created_at=job_created_at,
            root_dir=root_dir,
            archives=tuple(archives_by_id.values()),
        )

    def _remote_paths_for_archive(self, *, job_id: str, archive_id: str) -> dict[str, dict[str, Any]]:
        return {
            str(row.get("object_type", "")): row
            for row in self._store.list_remote_objects_for_cleanup(job_id=job_id)
            if str(row.get("archive_id", "")) == archive_id
        }

    def _write_session_and_parts(self, session_payload: dict[str, Any], plan: FileBlockPlan) -> None:
        with self._store.transaction() as conn:
            self._store.put_upload_session(
                conn,
                _versioned(
                    session_payload,
                    self._updated_by_device_id,
                    data_version=self._store.next_data_version(conn, "upload_sessions", "upload_session_id", session_payload["upload_session_id"]),
                ),
            )
            for part in plan.parts:
                part_id = _part_id(session_payload["upload_session_id"], part.partseq)
                self._store.put_upload_part(
                    conn,
                    _versioned(
                        {
                            "upload_part_id": part_id,
                            "entity_id": _part_entity_id(session_payload["upload_session_id"], part.partseq),
                            "upload_session_id": session_payload["upload_session_id"],
                            "partseq": part.partseq,
                            "offset": part.offset,
                            "size": part.size,
                            "md5": part.md5,
                            "status": "pending",
                            "attempt_count": 0,
                            "uploaded_at": None,
                            "confirmed_at": None,
                            "last_error": "",
                            "created_at": utc_now_iso(),
                        },
                        self._updated_by_device_id,
                        data_version=self._store.next_data_version(conn, "upload_parts", "upload_part_id", part_id),
                    ),
                )

    def _write_session(self, session_payload: dict[str, Any]) -> None:
        with self._store.transaction() as conn:
            self._store.put_upload_session(
                conn,
                _versioned(
                    session_payload,
                    self._updated_by_device_id,
                    data_version=self._store.next_data_version(conn, "upload_sessions", "upload_session_id", session_payload["upload_session_id"]),
                ),
            )

    def _write_part_status(self, upload_session_id: str, plan: FileBlockPlan, partseq: int, status: str) -> None:
        part = plan.part_by_seq(partseq)
        now = utc_now_iso()
        part_id = _part_id(upload_session_id, partseq)
        with self._store.transaction() as conn:
            existing = conn.execute(
                "SELECT attempt_count, created_at FROM upload_parts WHERE upload_part_id = ?",
                (part_id,),
            ).fetchone()
            attempt_count = int(existing["attempt_count"]) if existing is not None else 0
            if status == "uploading":
                attempt_count += 1
            self._store.put_upload_part(
                conn,
                _versioned(
                    {
                        "upload_part_id": part_id,
                        "entity_id": _part_entity_id(upload_session_id, partseq),
                        "upload_session_id": upload_session_id,
                        "partseq": part.partseq,
                        "offset": part.offset,
                        "size": part.size,
                        "md5": part.md5,
                        "status": status,
                        "attempt_count": attempt_count,
                        "uploaded_at": now if status in {"uploaded", "confirmed"} else None,
                        "confirmed_at": now if status == "confirmed" else None,
                        "last_error": "",
                        "created_at": str(existing["created_at"]) if existing is not None else now,
                    },
                    self._updated_by_device_id,
                    data_version=self._store.next_data_version(conn, "upload_parts", "upload_part_id", part_id),
                ),
            )

    def _write_remote_objects(
        self,
        *,
        job_id: str,
        device_id: str,
        archive_id: str,
        archive_sha256: str,
        archive_path: str,
        archive_size: int,
        archive_md5: str,
        archive_fs_id: int,
        meta_path: str,
        meta_document: StableJsonDocument,
        meta_md5: str,
        meta_fs_id: int,
        job_index_path: str,
        job_index: StableJsonDocument,
        job_index_md5: str,
        job_index_fs_id: int,
    ) -> None:
        with self._store.transaction() as conn:
            for payload in (
                _remote_object_payload(
                    object_type="archive",
                    job_id=job_id,
                    device_id=device_id,
                    archive_id=archive_id,
                    archive_sha256=archive_sha256,
                    remote_path=archive_path,
                    size_bytes=archive_size,
                    md5=archive_md5,
                    sha256=archive_sha256,
                    fs_id=archive_fs_id,
                ),
                _remote_object_payload(
                    object_type="archive_meta",
                    job_id=job_id,
                    device_id=device_id,
                    archive_id=archive_id,
                    archive_sha256=archive_sha256,
                    remote_path=meta_path,
                    size_bytes=len(meta_document.bytes),
                    md5=meta_md5 or hashlib.md5(meta_document.bytes).hexdigest(),
                    sha256=meta_document.sha256,
                    fs_id=meta_fs_id,
                ),
                _remote_object_payload(
                    object_type="job_index",
                    job_id=job_id,
                    device_id=device_id,
                    archive_id="",
                    archive_sha256="",
                    remote_path=job_index_path,
                    size_bytes=len(job_index.bytes),
                    md5=job_index_md5 or hashlib.md5(job_index.bytes).hexdigest(),
                    sha256=job_index.sha256,
                    fs_id=job_index_fs_id,
                ),
            ):
                self._store.put_remote_object(
                    conn,
                    _versioned(
                        payload,
                        self._updated_by_device_id,
                        data_version=self._store.next_data_version(conn, "remote_objects", "remote_object_id", payload["remote_object_id"]),
                    ),
                )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with local_fs.open_file(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _session_payload(
    *,
    upload_session_id: str,
    job_id: str,
    device_id: str,
    account_id: str,
    archive_id: str,
    archive_seq: int,
    archive_sha256: str,
    archive_md5: str,
    archive_size: int,
    archive_type: str,
    local_archive_path: str,
    remote_archive_path: str,
    remote_meta_path: str,
    remote_job_index_path: str,
    part_size: int,
    total_parts: int,
    block_md5s: tuple[str, ...],
    uploadid: str,
    upload_status: str,
    fs_id: int,
    remote_md5: str,
    error_code: str,
    error_message: str,
) -> dict[str, Any]:
    now = utc_now_iso()
    return {
        "upload_session_id": upload_session_id,
        "entity_id": f"upload_session_{upload_session_id}",
        "job_id": job_id,
        "device_id": device_id,
        "account_id": account_id,
        "archive_id": archive_id,
        "archive_seq": archive_seq,
        "archive_sha256": archive_sha256,
        "archive_md5": archive_md5,
        "archive_size": archive_size,
        "archive_type": archive_type,
        "local_archive_path": local_archive_path,
        "remote_archive_path": remote_archive_path,
        "remote_meta_path": remote_meta_path,
        "remote_job_index_path": remote_job_index_path,
        "part_size": part_size,
        "total_parts": total_parts,
        "block_md5s_json": json.dumps(list(block_md5s), separators=(",", ":")),
        "uploadid": uploadid,
        "upload_status": upload_status,
        "meta_status": "pending",
        "job_index_status": "pending",
        "fs_id": fs_id,
        "remote_md5": remote_md5,
        "error_code": error_code,
        "error_message": error_message,
        "completed_at": None,
        "created_at": now,
    }


def _remote_object_payload(
    *,
    object_type: str,
    job_id: str,
    device_id: str,
    archive_id: str,
    archive_sha256: str,
    remote_path: str,
    size_bytes: int,
    md5: str,
    sha256: str,
    fs_id: int,
) -> dict[str, Any]:
    now = utc_now_iso()
    remote_object_id = f"remote_{hashlib.sha256(remote_path.encode('utf-8')).hexdigest()}"
    return {
        "remote_object_id": remote_object_id,
        "entity_id": f"remote_object_{remote_object_id}",
        "object_type": object_type,
        "job_id": job_id,
        "device_id": device_id,
        "archive_id": archive_id,
        "archive_sha256": archive_sha256,
        "remote_path": remote_path,
        "size_bytes": size_bytes,
        "md5": md5,
        "sha256": sha256,
        "fs_id": fs_id,
        "status": "remote_created",
        "created_at": now,
    }


def _versioned(payload: dict[str, Any], updated_by_device_id: str, *, data_version: int = 1) -> dict[str, Any]:
    return build_version_fields(
        entity_payload=payload,
        updated_by_device_id=updated_by_device_id,
        data_version=data_version,
        schema_version=1,
    )


def _missing_partseqs(precreate: PrecreateResult, *, total_parts: int) -> tuple[int, ...]:
    missing = tuple(sorted(precreate.block_list))
    if any(partseq < 0 or partseq >= total_parts for partseq in missing):
        raise BaiduNetdiskError("baidu precreate block_list contains invalid partseq", error_code="invalid_response")
    return missing


def _session_id(job_id: str, archive_sha256: str, archive_seq: int) -> str:
    raw = f"{job_id}\0{archive_seq}\0{archive_sha256}".encode("utf-8")
    return "upload_" + hashlib.sha256(raw).hexdigest()


def _part_id(upload_session_id: str, partseq: int) -> str:
    return f"{upload_session_id}_part_{partseq:06d}"


def _part_entity_id(upload_session_id: str, partseq: int) -> str:
    return f"upload_part_{upload_session_id}_{partseq:06d}"


def _file_mtime_seconds(path: Path) -> int:
    return local_fs.mtime_seconds(path)
