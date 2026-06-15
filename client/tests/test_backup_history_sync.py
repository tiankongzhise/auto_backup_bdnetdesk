from __future__ import annotations

from auto_backup_client.backup_history_sync import sync_device_backup_history
from auto_backup_client.baidu.models import BackupHistoryEntity, BackupHistoryResponse
from auto_backup_client.restore_flow import RestoreService
from auto_backup_client.sqlite_store import LOCAL_ONLY_SYNC_FIELDS, SQLiteClientStore, sync_payload
from test_restore_flow import _completed_job


def test_sync_device_backup_history_rebuilds_source_restore_candidates_from_cloud_payload(tmp_path) -> None:
    source = tmp_path / "photos"
    nested = source / "nested" / "image.jpg"
    cover = source / "cover.jpg"
    nested.parent.mkdir(parents=True)
    nested.write_text("payload", encoding="utf-8")
    cover.write_text("cover", encoding="utf-8")
    original_store, job_id = _completed_job(tmp_path, source)
    entities = _history_entities_from_store(original_store, job_id=job_id)

    rebuilt_store = SQLiteClientStore(tmp_path / "rebuilt.sqlite3")
    rebuilt_store.migrate()
    result = sync_device_backup_history(store=rebuilt_store, cloud=_FakeHistoryCloud(entities))

    report = RestoreService(rebuilt_store, device_id="device-1", cache_root=tmp_path / "new-cache").list_candidates()
    candidate = report.candidates[0]
    assert result.imported_count == len(entities)
    assert result.skipped_count == 0
    assert len(report.candidates) == 1
    assert candidate.restore_candidate_id == candidate.backup_source_id
    assert candidate.source_type == "directory"
    assert candidate.source_display_name == "photos"
    assert candidate.file_count == 2
    assert candidate.size_bytes == len("payload") + len("cover")
    assert report.needs_download_count == 1
    assert candidate.remote_archive_status == "remote_created"

    with rebuilt_store.connect() as conn:
        source_row = conn.execute("SELECT local_path, sync_status FROM backup_sources").fetchone()
        archive_row = conn.execute("SELECT local_archive_path, sync_status FROM archives").fetchone()
    assert str(source_row["local_path"]).startswith("cloud-history://backup_source/")
    assert archive_row["local_archive_path"] == ""
    assert source_row["sync_status"] == "synced"
    assert archive_row["sync_status"] == "synced"


class _FakeHistoryCloud:
    def __init__(self, entities: tuple[BackupHistoryEntity, ...]) -> None:
        self.entities = entities
        self.limits: list[int] = []

    def list_backup_history(self, *, limit: int = 5000) -> BackupHistoryResponse:
        self.limits.append(limit)
        return BackupHistoryResponse(device_id="device-1", entities=self.entities)


def _history_entities_from_store(store: SQLiteClientStore, *, job_id: str) -> tuple[BackupHistoryEntity, ...]:
    tables = (
        ("backup_jobs", "entity_id"),
        ("backup_sources", "entity_id"),
        ("file_items", "entity_id"),
        ("folder_items", "entity_id"),
        ("content_objects", "entity_id"),
        ("content_references", "entity_id"),
        ("archives", "entity_id"),
        ("archive_members", "entity_id"),
        ("remote_objects", "entity_id"),
    )
    entities: list[BackupHistoryEntity] = []
    with store.connect() as conn:
        for table, key in tables:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            for row in rows:
                payload = sync_payload(dict(row))
                if table == "backup_jobs" and payload.get("backup_job_id") != job_id:
                    continue
                if table != "content_objects" and payload.get("job_id", payload.get("backup_job_id")) not in {"", job_id}:
                    continue
                for local_key in LOCAL_ONLY_SYNC_FIELDS:
                    payload.pop(local_key, None)
                entities.append(
                    BackupHistoryEntity(
                        entity_id=str(payload[key]),
                        entity_type=table,
                        data_version=int(payload["data_version"]),
                        revision_id=str(payload["revision_id"]),
                        canonical_record_sha256=str(payload["canonical_record_sha256"]),
                        updated_by_device_id=str(payload["updated_by_device_id"]),
                        payload=payload,
                    )
                )
    return tuple(entities)
