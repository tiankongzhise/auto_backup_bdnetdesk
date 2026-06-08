from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from auto_backup_client.sqlite_store import SQLiteClientStore


@dataclass(frozen=True)
class SourceMappingRow:
    backup_job_id: str
    job_name: str
    job_status: str
    device_id: str
    source_seq: int
    source_type: str
    source_display_name: str
    source_path_sha256: str
    file_item_id: str
    display_name: str
    relative_path: str
    relative_path_sha256: str
    size_bytes: int
    md5: str
    sha256: str
    content_id: str
    dedupe_status: str
    reference_role: str
    cleanup_status: str
    restore_status: str
    archive_id: str
    archive_seq: int | None
    archive_sha256: str
    archive_type: str
    archive_member_path: str
    remote_archive_path_sha256: str
    remote_meta_path_sha256: str
    remote_job_index_path_sha256: str
    remote_archive_status: str
    remote_archive_fs_id: int | None
    remote_meta_status: str
    job_index_status: str
    baidu_ready: bool


@dataclass(frozen=True)
class SourceMappingSummary:
    total_rows: int
    job_count: int
    source_count: int
    content_count: int
    archive_count: int
    remote_object_count: int
    baidu_ready_count: int


@dataclass(frozen=True)
class SourceMappingReport:
    rows: tuple[SourceMappingRow, ...]
    summary: SourceMappingSummary


class SourceMappingQuery:
    def __init__(self, store: SQLiteClientStore) -> None:
        self._store = store

    def list_rows(self, *, backup_job_id: str = "", keyword: str = "", limit: int = 500) -> SourceMappingReport:
        if limit < 1 or limit > 5000:
            raise ValueError("source mapping limit must be between 1 and 5000")
        cleaned_job_id = backup_job_id.strip()
        cleaned_keyword = " ".join(keyword.strip().split()).casefold()
        with self._store.connect() as conn:
            params: list[object] = []
            where = []
            if cleaned_job_id:
                where.append("j.backup_job_id = ?")
                params.append(cleaned_job_id)
            if cleaned_keyword:
                like = f"%{cleaned_keyword}%"
                where.append(
                    """
                    (
                        lower(j.job_name) LIKE ?
                        OR lower(s.display_name) LIKE ?
                        OR lower(f.display_name) LIKE ?
                        OR lower(f.relative_path) LIKE ?
                        OR lower(cr.content_id) LIKE ?
                        OR lower(COALESCE(a.archive_sha256, '')) LIKE ?
                    )
                    """
                )
                params.extend([like, like, like, like, like, like])
            where_sql = "WHERE " + " AND ".join(where) if where else ""
            rows = conn.execute(
                f"""
                SELECT
                    j.backup_job_id,
                    j.job_name,
                    j.status AS job_status,
                    j.device_id,
                    s.source_seq,
                    s.source_type,
                    s.display_name AS source_display_name,
                    s.path_sha256 AS source_path_sha256,
                    f.file_item_id,
                    f.display_name,
                    f.relative_path,
                    f.relative_path_sha256,
                    f.size_bytes,
                    f.md5,
                    f.sha256,
                    f.content_id,
                    cr.dedupe_status,
                    cr.reference_role,
                    cr.cleanup_status,
                    cr.restore_status,
                    cr.archive_id,
                    cr.archive_sha256 AS reference_archive_sha256,
                    cr.archive_member_path,
                    a.archive_seq,
                    a.archive_sha256,
                    a.archive_type,
                    ro_archive.remote_path AS remote_archive_path,
                    ro_archive.status AS remote_archive_status,
                    ro_archive.fs_id AS remote_archive_fs_id,
                    ro_meta.remote_path AS remote_meta_path,
                    ro_meta.status AS remote_meta_status,
                    ro_job.remote_path AS remote_job_index_path,
                    ro_job.status AS job_index_status
                FROM backup_jobs j
                JOIN backup_sources s ON s.backup_job_id = j.backup_job_id
                LEFT JOIN file_items f
                    ON f.backup_job_id = j.backup_job_id
                   AND f.backup_source_id = s.backup_source_id
                LEFT JOIN content_references cr
                    ON cr.file_item_id = f.file_item_id
                LEFT JOIN archives a
                    ON a.archive_id = cr.archive_id
                LEFT JOIN remote_objects ro_archive
                    ON ro_archive.archive_id = cr.archive_id
                   AND ro_archive.object_type = 'archive'
                LEFT JOIN remote_objects ro_meta
                    ON ro_meta.archive_id = cr.archive_id
                   AND ro_meta.object_type = 'archive_meta'
                LEFT JOIN remote_objects ro_job
                    ON ro_job.job_id = j.backup_job_id
                   AND ro_job.object_type = 'job_index'
                {where_sql}
                ORDER BY j.created_at DESC, s.source_seq, f.relative_path, cr.content_reference_id
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
        mapped_rows = tuple(_row_from_sql(row) for row in rows if row["file_item_id"])
        job_ids = {row.backup_job_id for row in mapped_rows}
        source_keys = {(row.backup_job_id, row.source_seq) for row in mapped_rows}
        content_ids = {row.content_id for row in mapped_rows if row.content_id}
        archive_ids = {row.archive_id for row in mapped_rows if row.archive_id}
        remote_keys = {
            key
            for row in mapped_rows
            for key in (row.remote_archive_path_sha256, row.remote_meta_path_sha256, row.remote_job_index_path_sha256)
            if key and key != "not_uploaded"
        }
        return SourceMappingReport(
            rows=mapped_rows,
            summary=SourceMappingSummary(
                total_rows=len(mapped_rows),
                job_count=len(job_ids),
                source_count=len(source_keys),
                content_count=len(content_ids),
                archive_count=len(archive_ids),
                remote_object_count=len(remote_keys),
                baidu_ready_count=sum(1 for row in mapped_rows if row.baidu_ready),
            ),
        )


def path_digest(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return ""
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()


def short_digest(value: str, *, length: int = 12) -> str:
    cleaned = value.strip()
    return cleaned[:length] if cleaned else ""


def display_filename(path_or_name: str) -> str:
    cleaned = path_or_name.strip()
    if not cleaned:
        return ""
    return Path(cleaned).name or cleaned


def _row_from_sql(row) -> SourceMappingRow:  # type: ignore[no-untyped-def]
    remote_archive_status = str(row["remote_archive_status"] or "not_uploaded")
    remote_meta_status = str(row["remote_meta_status"] or "not_uploaded")
    job_index_status = str(row["job_index_status"] or "not_uploaded")
    archive_sha256 = str(row["archive_sha256"] or row["reference_archive_sha256"] or "")
    baidu_ready = (
        bool(row["remote_archive_path"])
        and remote_archive_status == "remote_created"
        and remote_meta_status == "remote_created"
        and job_index_status == "remote_created"
    )
    return SourceMappingRow(
        backup_job_id=str(row["backup_job_id"]),
        job_name=str(row["job_name"]),
        job_status=str(row["job_status"]),
        device_id=str(row["device_id"]),
        source_seq=int(row["source_seq"]),
        source_type=str(row["source_type"]),
        source_display_name=str(row["source_display_name"]),
        source_path_sha256=str(row["source_path_sha256"]),
        file_item_id=str(row["file_item_id"]),
        display_name=str(row["display_name"]),
        relative_path=str(row["relative_path"]),
        relative_path_sha256=str(row["relative_path_sha256"]),
        size_bytes=int(row["size_bytes"] or 0),
        md5=str(row["md5"] or ""),
        sha256=str(row["sha256"] or ""),
        content_id=str(row["content_id"] or ""),
        dedupe_status=str(row["dedupe_status"] or "not_indexed"),
        reference_role=str(row["reference_role"] or "not_indexed"),
        cleanup_status=str(row["cleanup_status"] or "not_cleaned"),
        restore_status=str(row["restore_status"] or "not_restored"),
        archive_id=str(row["archive_id"] or ""),
        archive_seq=int(row["archive_seq"]) if row["archive_seq"] is not None else None,
        archive_sha256=archive_sha256,
        archive_type=str(row["archive_type"] or "not_packaged"),
        archive_member_path=str(row["archive_member_path"] or ""),
        remote_archive_path_sha256=path_digest(str(row["remote_archive_path"] or "")),
        remote_meta_path_sha256=path_digest(str(row["remote_meta_path"] or "")),
        remote_job_index_path_sha256=path_digest(str(row["remote_job_index_path"] or "")),
        remote_archive_status=remote_archive_status,
        remote_archive_fs_id=int(row["remote_archive_fs_id"]) if row["remote_archive_fs_id"] is not None else None,
        remote_meta_status=remote_meta_status,
        job_index_status=job_index_status,
        baidu_ready=baidu_ready,
    )
