from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence

from auto_backup_client.sqlite_store import SQLiteClientStore, build_version_fields, new_id, utc_now_iso


BackupSourceType = Literal["file", "directory"]

JOB_STATUS_LABELS = {
    "queued": "待开始",
    "running": "运行中",
    "paused": "已暂停",
    "canceled": "已取消",
    "completed": "已完成",
    "failed_retryable": "等待重试",
    "failed_terminal": "失败",
}

VALID_STATUS_TRANSITIONS = {
    "queued": {"running", "canceled"},
    "running": {"paused", "canceled", "completed", "failed_retryable", "failed_terminal"},
    "paused": {"running", "canceled"},
    "failed_retryable": {"running", "canceled", "failed_terminal"},
    "canceled": set(),
    "completed": set(),
    "failed_terminal": set(),
}


@dataclass(frozen=True)
class BackupSourceInput:
    local_path: str
    source_type: BackupSourceType | None = None


@dataclass(frozen=True)
class BackupSourceRecord:
    backup_source_id: str
    backup_job_id: str
    source_seq: int
    source_type: BackupSourceType
    local_path: str
    display_name: str
    path_sha256: str
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class BackupJobRecord:
    backup_job_id: str
    entity_id: str
    device_id: str
    job_name: str
    status: str
    source_count: int
    data_version: int
    revision_id: str
    updated_at: str
    sync_status: str
    created_at: str


@dataclass(frozen=True)
class BackupJobWithSources:
    job: BackupJobRecord
    sources: tuple[BackupSourceRecord, ...]


class BackupJobError(ValueError):
    pass


class BackupJobManager:
    def __init__(self, store: SQLiteClientStore, *, device_id: str) -> None:
        cleaned_device_id = device_id.strip()
        if not cleaned_device_id:
            raise BackupJobError("device_id is required")
        self.store = store
        self.device_id = cleaned_device_id

    def create_job(
        self,
        sources: Sequence[BackupSourceInput | str],
        *,
        job_name: str = "",
        now: str | None = None,
    ) -> BackupJobWithSources:
        normalized_sources = normalize_sources(sources)
        actual_now = now or utc_now_iso()
        backup_job_id = new_id("job")
        cleaned_name = _clean_job_name(job_name) or default_job_name(actual_now)
        job_payload = build_version_fields(
            entity_payload={
                "backup_job_id": backup_job_id,
                "entity_id": f"backup_job_{backup_job_id}",
                "device_id": self.device_id,
                "job_name": cleaned_name,
                "status": "queued",
                "source_count": len(normalized_sources),
                "started_at": None,
                "paused_at": None,
                "canceled_at": None,
                "completed_at": None,
                "created_at": actual_now,
            },
            updated_by_device_id=self.device_id,
            now=actual_now,
            sync_status="sync_pending",
        )
        with self.store.transaction() as conn:
            self.store.put_backup_job(conn, job_payload)
            for index, source in enumerate(normalized_sources, start=1):
                source_payload = {
                    "backup_source_id": new_id("source"),
                    "backup_job_id": backup_job_id,
                    "source_seq": index,
                    "source_type": source.source_type,
                    "local_path": source.local_path,
                    "display_name": _display_name(source.local_path),
                    "path_sha256": path_sha256(source.local_path),
                    "status": "pending",
                    "created_at": actual_now,
                    "updated_at": actual_now,
                }
                self.store.put_backup_source(conn, source_payload)
        return self.get_job_with_sources(backup_job_id)

    def transition_job(
        self,
        backup_job_id: str,
        next_status: str,
        *,
        now: str | None = None,
    ) -> BackupJobWithSources:
        cleaned_job_id = backup_job_id.strip()
        cleaned_status = next_status.strip()
        if not cleaned_job_id:
            raise BackupJobError("backup_job_id is required")
        if cleaned_status not in JOB_STATUS_LABELS:
            raise BackupJobError(f"unsupported backup job status: {cleaned_status}")
        actual_now = now or utc_now_iso()
        with self.store.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM backup_jobs WHERE backup_job_id = ?",
                (cleaned_job_id,),
            ).fetchone()
            if row is None:
                raise BackupJobError("backup job not found")
            current = str(row["status"])
            if cleaned_status == current:
                raise BackupJobError("backup job is already in requested status")
            if cleaned_status not in VALID_STATUS_TRANSITIONS.get(current, set()):
                raise BackupJobError(f"invalid backup job status transition: {current} -> {cleaned_status}")

            payload = dict(row)
            payload["status"] = cleaned_status
            if cleaned_status == "running" and current in {"queued", "failed_retryable"}:
                payload["started_at"] = payload.get("started_at") or actual_now
            if cleaned_status == "paused":
                payload["paused_at"] = actual_now
            elif cleaned_status == "running":
                payload["paused_at"] = None
            elif cleaned_status == "canceled":
                payload["canceled_at"] = actual_now
            elif cleaned_status == "completed":
                payload["completed_at"] = actual_now

            versioned = build_version_fields(
                entity_payload=payload,
                updated_by_device_id=self.device_id,
                data_version=self.store.next_data_version(
                    conn,
                    "backup_jobs",
                    "backup_job_id",
                    cleaned_job_id,
                ),
                schema_version=int(payload["schema_version"]),
                now=actual_now,
                sync_status="sync_pending",
                deleted_at=payload.get("deleted_at"),
                last_synced_revision_id=payload.get("last_synced_revision_id"),
            )
            self.store.put_backup_job(conn, versioned)
        return self.get_job_with_sources(cleaned_job_id)

    def list_jobs(self, *, limit: int = 100) -> list[BackupJobWithSources]:
        jobs = self.store.list_backup_jobs(limit=limit)
        return [self.get_job_with_sources(str(job["backup_job_id"])) for job in jobs]

    def get_job_with_sources(self, backup_job_id: str) -> BackupJobWithSources:
        row = self.store.get_backup_job(backup_job_id)
        if row is None:
            raise BackupJobError("backup job not found")
        sources = self.store.list_backup_sources(backup_job_id)
        return BackupJobWithSources(
            job=_job_from_row(row),
            sources=tuple(_source_from_row(source) for source in sources),
        )


def normalize_sources(sources: Sequence[BackupSourceInput | str]) -> tuple[BackupSourceInput, ...]:
    if not sources:
        raise BackupJobError("at least one backup source is required")
    normalized: list[BackupSourceInput] = []
    seen: set[str] = set()
    for source in sources:
        if isinstance(source, BackupSourceInput):
            raw_path = source.local_path
            source_type = source.source_type
        else:
            raw_path = str(source)
            source_type = None
        local_path = normalize_local_path(raw_path)
        key = local_path.casefold() if os.name == "nt" else local_path
        if key in seen:
            raise BackupJobError("duplicate backup source is not allowed")
        seen.add(key)
        normalized.append(BackupSourceInput(local_path=local_path, source_type=source_type or detect_source_type(local_path)))
    return tuple(normalized)


def normalize_local_path(value: str) -> str:
    cleaned = str(value).strip().strip('"')
    if not cleaned:
        raise BackupJobError("backup source path is required")
    return str(Path(cleaned).expanduser())


def detect_source_type(local_path: str) -> BackupSourceType:
    return "directory" if Path(local_path).is_dir() else "file"


def path_sha256(local_path: str) -> str:
    return hashlib.sha256(local_path.encode("utf-8")).hexdigest()


def status_label(status: str) -> str:
    return JOB_STATUS_LABELS.get(status, status)


def default_job_name(now: str) -> str:
    cleaned = now.replace("T", " ").replace("Z", "")
    return f"备份任务 {cleaned[:19]}"


def _clean_job_name(value: str) -> str:
    return " ".join(value.strip().split())


def _display_name(local_path: str) -> str:
    path = Path(local_path)
    return path.name or str(path)


def _job_from_row(row: dict[str, object]) -> BackupJobRecord:
    return BackupJobRecord(
        backup_job_id=str(row["backup_job_id"]),
        entity_id=str(row["entity_id"]),
        device_id=str(row["device_id"]),
        job_name=str(row["job_name"]),
        status=str(row["status"]),
        source_count=int(row["source_count"]),
        data_version=int(row["data_version"]),
        revision_id=str(row["revision_id"]),
        updated_at=str(row["updated_at"]),
        sync_status=str(row["sync_status"]),
        created_at=str(row["created_at"]),
    )


def _source_from_row(row: dict[str, object]) -> BackupSourceRecord:
    return BackupSourceRecord(
        backup_source_id=str(row["backup_source_id"]),
        backup_job_id=str(row["backup_job_id"]),
        source_seq=int(row["source_seq"]),
        source_type=str(row["source_type"]),  # type: ignore[arg-type]
        local_path=str(row["local_path"]),
        display_name=str(row["display_name"]),
        path_sha256=str(row["path_sha256"]),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )
