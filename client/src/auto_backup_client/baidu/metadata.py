from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence


ARCHIVE_META_VERSION = 1
JOB_INDEX_VERSION = 1
DEFAULT_CLIENT_VERSION = "0.1.0"


SENSITIVE_HINTS = (
    "access_token",
    "refresh_token",
    "device_token",
    "wrapping_key",
    "password",
    "secret",
    "original_path",
    "original_name",
)


@dataclass(frozen=True)
class StableJsonDocument:
    data: dict[str, Any]
    text: str
    sha256: str

    @property
    def bytes(self) -> bytes:
        return self.text.encode("utf-8")


@dataclass(frozen=True)
class ArchiveMetaInput:
    archive_id: str
    archive_seq: int
    archive_sha256: str
    archive_md5: str
    archive_size: int
    archive_type: str
    job_id: str
    device_id: str
    manifest_id: str
    created_at: datetime
    client_version: str = DEFAULT_CLIENT_VERSION


@dataclass(frozen=True)
class JobIndexArchive:
    archive_id: str
    archive_seq: int
    archive_sha256: str
    archive_size: int
    archive_type: str
    remote_archive_path: str
    remote_meta_path: str
    fs_id: int = 0
    meta_sha256: str = ""


def build_archive_meta_document(value: ArchiveMetaInput) -> StableJsonDocument:
    data = {
        "meta_version": ARCHIVE_META_VERSION,
        "archive_id": value.archive_id,
        "archive_seq": int(value.archive_seq),
        "archive_sha256": value.archive_sha256,
        "archive_md5": value.archive_md5,
        "archive_size": int(value.archive_size),
        "archive_type": value.archive_type,
        "job_id": value.job_id,
        "device_id": value.device_id,
        "manifest_id": value.manifest_id,
        "created_at": _format_datetime(value.created_at),
        "client_version": value.client_version,
    }
    _reject_sensitive_keys(data)
    return stable_json_document(data)


def build_job_index_document(
    *,
    job_id: str,
    device_id: str,
    job_created_at: datetime,
    root_dir: str,
    archives: Sequence[JobIndexArchive],
    client_version: str = DEFAULT_CLIENT_VERSION,
) -> StableJsonDocument:
    data = {
        "index_version": JOB_INDEX_VERSION,
        "job_id": job_id,
        "device_id": device_id,
        "job_created_at": _format_datetime(job_created_at),
        "root_dir": _normalize_root(root_dir),
        "client_version": client_version,
        "archives": [
            {
                "archive_id": archive.archive_id,
                "archive_seq": int(archive.archive_seq),
                "archive_sha256": archive.archive_sha256,
                "archive_size": int(archive.archive_size),
                "archive_type": archive.archive_type,
                "remote_archive_path": archive.remote_archive_path,
                "remote_meta_path": archive.remote_meta_path,
                "fs_id": int(archive.fs_id),
                "meta_sha256": archive.meta_sha256,
            }
            for archive in sorted(archives, key=lambda item: item.archive_seq)
        ],
    }
    _reject_sensitive_keys(data)
    return stable_json_document(data)


def stable_json_document(data: Mapping[str, Any]) -> StableJsonDocument:
    text = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return StableJsonDocument(data=dict(data), text=text, sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())


def _format_datetime(value: datetime) -> str:
    actual = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return actual.isoformat().replace("+00:00", "Z")


def _normalize_root(root_dir: str) -> str:
    cleaned = str(PurePosixPath(str(root_dir).replace("\\", "/")))
    return cleaned.rstrip("/") if cleaned != "/" else cleaned


def _reject_sensitive_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered = str(key).lower()
            if any(hint in lowered for hint in SENSITIVE_HINTS):
                raise ValueError(f"metadata contains sensitive field: {key}")
            _reject_sensitive_keys(item)
    elif isinstance(value, list):
        for item in value:
            _reject_sensitive_keys(item)
