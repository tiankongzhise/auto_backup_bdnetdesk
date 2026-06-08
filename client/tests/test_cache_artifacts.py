from __future__ import annotations

from pathlib import Path

import pytest

from auto_backup_client.cache_artifacts import (
    CacheArtifactError,
    CacheArtifactManager,
    CacheBudget,
    classify_cache_level,
)
from auto_backup_client.sqlite_store import SQLiteClientStore


def test_cache_artifact_registers_status_and_cleanup_without_leaking_paths(tmp_path) -> None:
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    cache_root = tmp_path / "cache"
    artifact = cache_root / "jobs" / "short" / "tmp" / "payload.bin"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"payload")
    manager = CacheArtifactManager(store, cache_root=cache_root)

    row = manager.register_path(
        path=artifact,
        artifact_type="staging",
        job_id="job-1",
        required_until_stage="verified",
        now="2026-06-08T10:00:00Z",
    )
    dry_run = manager.cleanup(current_stage="completed", cache_level="sufficient", dry_run=True)

    assert row["size_bytes"] == 7
    assert row["path_sha256"] in dry_run.path_sha256s
    assert str(artifact) not in dry_run.path_sha256s
    assert artifact.exists()

    result = manager.cleanup(current_stage="completed", cache_level="sufficient", dry_run=False)

    assert result.deleted_count == 1
    assert result.freed_bytes == 7
    assert not artifact.exists()
    assert store.list_cache_artifacts(include_deleted=True)[0]["lifecycle_status"] == "deleted"


def test_cache_cleanup_keeps_unconfirmed_archive_and_rejects_outside_cache(tmp_path) -> None:
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    cache_root = tmp_path / "cache"
    archive = cache_root / "jobs" / "short" / "archives" / "000001-a.7z"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"archive")
    manager = CacheArtifactManager(store, cache_root=cache_root)
    manager.register_path(
        path=archive,
        artifact_type="archive",
        job_id="job-1",
        required_until_stage="completed",
        remote_confirmed=False,
    )

    assert manager.cleanup(current_stage="completed", cache_level="critical", dry_run=False).deleted_count == 0
    assert archive.exists()
    with pytest.raises(CacheArtifactError, match="inside cache root"):
        manager.register_path(
            path=tmp_path / "source.txt",
            artifact_type="tmp",
            job_id="job-1",
            required_until_stage="completed",
        )


def test_cache_usage_blocks_when_effective_budget_below_minimum(tmp_path) -> None:
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    manager = CacheArtifactManager(store, cache_root=cache_root)

    with pytest.raises(CacheArtifactError, match="effective budget"):
        manager.ensure_can_start(
            CacheBudget(
                cache_root=cache_root,
                cache_quota_bytes=1,
                min_effective_budget_bytes=2,
                max_archive_size_bytes=1,
                reserve_bytes=0,
            )
        )


def test_cache_level_classification_boundaries() -> None:
    assert classify_cache_level(used_bytes=50, cache_quota_bytes=100, disk_free_bytes=1_000, max_archive_size_bytes=100, reserve_bytes=0) == "sufficient"
    assert classify_cache_level(used_bytes=70, cache_quota_bytes=100, disk_free_bytes=1_000, max_archive_size_bytes=100, reserve_bytes=0) == "medium"
    assert classify_cache_level(used_bytes=85, cache_quota_bytes=100, disk_free_bytes=1_000, max_archive_size_bytes=100, reserve_bytes=0) == "tight"
    assert classify_cache_level(used_bytes=95, cache_quota_bytes=100, disk_free_bytes=1_000, max_archive_size_bytes=100, reserve_bytes=0) == "critical"
