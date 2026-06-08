from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from auto_backup_client import local_fs
from auto_backup_client.sqlite_store import SQLiteClientStore, utc_now_iso


GIB = 1024**3
DEFAULT_MIN_EFFECTIVE_BUDGET_BYTES = 40 * GIB
DEFAULT_CACHE_QUOTA_BYTES = 40 * GIB
DEFAULT_MAX_ARCHIVE_SIZE_BYTES = 4 * GIB
KEEP_COMPLETED_ARCHIVE_STAGE = "completed"
STAGE_ORDER = {
    "packaged": 10,
    "verified": 20,
    "uploaded": 30,
    "remote_confirmed": 40,
    "completed": 50,
    "strict_verified": 60,
    "restore_completed": 70,
}


class CacheArtifactError(ValueError):
    pass


@dataclass(frozen=True)
class CacheBudget:
    cache_root: Path
    cache_quota_bytes: int = DEFAULT_CACHE_QUOTA_BYTES
    min_effective_budget_bytes: int = DEFAULT_MIN_EFFECTIVE_BUDGET_BYTES
    max_archive_size_bytes: int = DEFAULT_MAX_ARCHIVE_SIZE_BYTES
    reserve_bytes: int = 10 * GIB


@dataclass(frozen=True)
class CacheUsage:
    cache_root: Path
    cache_quota_bytes: int
    used_bytes: int
    active_bytes: int
    releasable_bytes: int
    disk_free_bytes: int
    effective_budget_bytes: int
    level: str
    can_start_new_job: bool
    reason: str = ""


@dataclass(frozen=True)
class CacheCleanupResult:
    dry_run: bool
    selected_count: int
    deleted_count: int
    freed_bytes: int
    path_sha256s: tuple[str, ...]


class CacheArtifactManager:
    def __init__(self, store: SQLiteClientStore, *, cache_root: str | Path) -> None:
        self.store = store
        self.cache_root = Path(cache_root).expanduser().resolve()

    def register_path(
        self,
        *,
        path: str | Path,
        artifact_type: str,
        job_id: str = "",
        required_until_stage: str,
        deletable: bool = True,
        remote_confirmed: bool = False,
        now: str | None = None,
    ) -> dict[str, object]:
        actual_path = Path(path).expanduser().resolve()
        self._ensure_inside_cache(actual_path)
        actual_now = now or utc_now_iso()
        path_text = str(actual_path)
        path_sha256 = _sha256_text(path_text)
        payload = {
            "artifact_id": f"artifact_{path_sha256}",
            "job_id": job_id.strip(),
            "artifact_type": artifact_type,
            "artifact_path": path_text,
            "path_sha256": path_sha256,
            "size_bytes": _artifact_size(actual_path),
            "required_until_stage": required_until_stage,
            "lifecycle_status": "active" if local_fs.exists(actual_path) else "missing",
            "deletable": 1 if deletable else 0,
            "remote_confirmed": 1 if remote_confirmed else 0,
            "created_at": actual_now,
            "last_accessed_at": actual_now,
            "deleted_at": None,
        }
        with self.store.transaction() as conn:
            self.store.put_cache_artifact(conn, payload)
        return payload

    def register_many(self, artifacts: Iterable[Mapping[str, object]], *, now: str | None = None) -> None:
        for artifact in artifacts:
            self.register_path(
                path=artifact["path"],
                artifact_type=str(artifact["artifact_type"]),
                job_id=str(artifact.get("job_id", "")),
                required_until_stage=str(artifact["required_until_stage"]),
                deletable=bool(artifact.get("deletable", True)),
                remote_confirmed=bool(artifact.get("remote_confirmed", False)),
                now=now,
            )

    def usage(self, budget: CacheBudget | None = None) -> CacheUsage:
        actual_budget = budget or CacheBudget(cache_root=self.cache_root)
        artifacts = self.store.list_cache_artifacts(include_deleted=False)
        active_bytes = 0
        releasable_bytes = 0
        with self.store.transaction() as conn:
            for artifact in artifacts:
                artifact_path = Path(str(artifact["artifact_path"]))
                self._ensure_inside_cache(artifact_path)
                size = _artifact_size(artifact_path)
                status = "active" if local_fs.exists(artifact_path) else "missing"
                self.store.update_cache_artifact_status(
                    conn,
                    artifact_id=str(artifact["artifact_id"]),
                    lifecycle_status=status,
                    size_bytes=size,
                    last_accessed_at=utc_now_iso(),
                )
                if status != "active":
                    continue
                active_bytes += size
                if _is_releasable(artifact, current_stage="completed", cache_level="medium"):
                    releasable_bytes += size
        disk_free = _disk_free_bytes(self.cache_root)
        used_bytes = _directory_size(self.cache_root)
        level = classify_cache_level(
            used_bytes=used_bytes,
            cache_quota_bytes=actual_budget.cache_quota_bytes,
            disk_free_bytes=disk_free,
            max_archive_size_bytes=actual_budget.max_archive_size_bytes,
            reserve_bytes=actual_budget.reserve_bytes,
        )
        effective_budget = min(max(0, actual_budget.cache_quota_bytes - used_bytes), disk_free)
        reason = ""
        if level == "critical":
            reason = "cache level is critical"
        elif effective_budget < actual_budget.min_effective_budget_bytes:
            reason = "cache effective budget is below minimum"
        return CacheUsage(
            cache_root=self.cache_root,
            cache_quota_bytes=actual_budget.cache_quota_bytes,
            used_bytes=used_bytes,
            active_bytes=active_bytes,
            releasable_bytes=releasable_bytes,
            disk_free_bytes=disk_free,
            effective_budget_bytes=effective_budget,
            level=level,
            can_start_new_job=not reason,
            reason=reason,
        )

    def ensure_can_start(self, budget: CacheBudget | None = None) -> CacheUsage:
        usage = self.usage(budget)
        if not usage.can_start_new_job:
            raise CacheArtifactError(usage.reason)
        return usage

    def cleanup(
        self,
        *,
        current_stage: str = "completed",
        cache_level: str = "medium",
        dry_run: bool = True,
        job_id: str = "",
        now: str | None = None,
    ) -> CacheCleanupResult:
        actual_now = now or utc_now_iso()
        selected = self._cleanup_candidates(current_stage=current_stage, cache_level=cache_level, job_id=job_id)
        deleted_count = 0
        freed_bytes = 0
        hashes: list[str] = []
        if dry_run:
            return CacheCleanupResult(
                dry_run=True,
                selected_count=len(selected),
                deleted_count=0,
                freed_bytes=sum(int(item["size_bytes"]) for item in selected),
                path_sha256s=tuple(str(item["path_sha256"]) for item in selected),
            )
        with self.store.transaction() as conn:
            for artifact in selected:
                path = Path(str(artifact["artifact_path"]))
                self._ensure_inside_cache(path)
                size_before = _artifact_size(path)
                if local_fs.exists(path):
                    if local_fs.is_dir(path):
                        local_fs.remove_tree(path)
                    else:
                        local_fs.unlink(path, missing_ok=True)
                self.store.update_cache_artifact_status(
                    conn,
                    artifact_id=str(artifact["artifact_id"]),
                    lifecycle_status="deleted",
                    size_bytes=0,
                    deleted_at=actual_now,
                    last_accessed_at=actual_now,
                )
                deleted_count += 1
                freed_bytes += size_before
                hashes.append(str(artifact["path_sha256"]))
        return CacheCleanupResult(
            dry_run=False,
            selected_count=len(selected),
            deleted_count=deleted_count,
            freed_bytes=freed_bytes,
            path_sha256s=tuple(hashes),
        )

    def _cleanup_candidates(self, *, current_stage: str, cache_level: str, job_id: str = "") -> list[dict[str, object]]:
        rows = self.store.list_cache_artifacts(job_id=job_id, include_deleted=False)
        result: list[dict[str, object]] = []
        for artifact in rows:
            path = Path(str(artifact["artifact_path"]))
            self._ensure_inside_cache(path)
            if _is_releasable(artifact, current_stage=current_stage, cache_level=cache_level):
                result.append(artifact)
        return result

    def _ensure_inside_cache(self, path: Path) -> None:
        try:
            path.resolve().relative_to(self.cache_root)
        except ValueError as exc:
            raise CacheArtifactError("cache artifact path must stay inside cache root") from exc


def classify_cache_level(
    *,
    used_bytes: int,
    cache_quota_bytes: int,
    disk_free_bytes: int,
    max_archive_size_bytes: int,
    reserve_bytes: int = 10 * GIB,
) -> str:
    quota = max(1, int(cache_quota_bytes))
    used_ratio = int(used_bytes) / quota
    if used_ratio > 0.9 or disk_free_bytes < max_archive_size_bytes + reserve_bytes:
        return "critical"
    if used_ratio > 0.8 or disk_free_bytes < int(max_archive_size_bytes * 1.5) + reserve_bytes:
        return "tight"
    if used_ratio > 0.6 or disk_free_bytes < 2 * max_archive_size_bytes + reserve_bytes:
        return "medium"
    return "sufficient"


def build_job_cache_dir(cache_root: str | Path, job_id: str) -> Path:
    digest = _sha256_text(job_id.strip())[:16]
    return Path(cache_root).expanduser().resolve() / "jobs" / digest


def default_artifact_specs(*, cache_root: str | Path, job_id: str, archive_seq: int, archive_path: str | Path) -> tuple[dict[str, object], ...]:
    job_cache = build_job_cache_dir(cache_root, job_id)
    return (
        {
            "path": job_cache / "manifest_plain",
            "artifact_type": "manifest_plain",
            "job_id": job_id,
            "required_until_stage": "verified",
            "deletable": True,
        },
        {
            "path": job_cache / "tmp" / f"archive_{archive_seq:06d}",
            "artifact_type": "staging",
            "job_id": job_id,
            "required_until_stage": "verified",
            "deletable": True,
        },
        {
            "path": job_cache / "verify" / f"archive_{archive_seq:06d}",
            "artifact_type": "verify",
            "job_id": job_id,
            "required_until_stage": "strict_verified",
            "deletable": True,
        },
        {
            "path": archive_path,
            "artifact_type": "archive",
            "job_id": job_id,
            "required_until_stage": "completed",
            "deletable": True,
        },
    )


def _is_releasable(artifact: Mapping[str, object], *, current_stage: str, cache_level: str) -> bool:
    if str(artifact.get("lifecycle_status", "")) != "active":
        return False
    if int(artifact.get("deletable", 0)) != 1:
        return False
    artifact_type = str(artifact.get("artifact_type", ""))
    required_until = str(artifact.get("required_until_stage", "completed"))
    if _stage_value(current_stage) < _stage_value(required_until):
        return False
    if artifact_type == "archive":
        if int(artifact.get("remote_confirmed", 0)) != 1:
            return False
        return cache_level in {"medium", "tight", "critical"}
    return artifact_type in {"manifest_plain", "staging", "verify", "tmp", "upload_temp", "download", "restore"}


def _stage_value(stage: str) -> int:
    return STAGE_ORDER.get(stage, -1)


def _artifact_size(path: Path) -> int:
    if not local_fs.exists(path):
        return 0
    if local_fs.is_dir(path):
        return _directory_size(path)
    return local_fs.file_size(path)


def _directory_size(path: Path) -> int:
    if not local_fs.exists(path):
        return 0
    if not local_fs.is_dir(path):
        return local_fs.file_size(path)
    total = 0
    for root, _dirs, files in os.walk(local_fs.native_path(path)):
        root_path = Path(root)
        for filename in files:
            file_path = root_path / filename
            try:
                total += local_fs.file_size(file_path)
            except FileNotFoundError:
                continue
    return total


def _disk_free_bytes(path: Path) -> int:
    local_fs.make_dirs(path)
    return int(shutil.disk_usage(local_fs.native_path(path)).free)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
