from __future__ import annotations

from datetime import datetime, timezone

import pytest

from auto_backup_client.baidu.cloud_api import CloudAPIError
from auto_backup_client.baidu.models import ContentObject
from auto_backup_client.backup_jobs import BackupJobManager, BackupSourceInput
from auto_backup_client.dedupe_index import ContentDedupeIndexer, DedupeIndexError
from auto_backup_client.scan_fingerprints import BackupScanner, file_content_id
from auto_backup_client.sqlite_store import SQLiteClientStore


def test_build_job_index_creates_one_content_object_and_multiple_references_without_paths_in_outbox(tmp_path) -> None:
    first = tmp_path / "first.txt"
    nested = tmp_path / "nested"
    nested.mkdir()
    second = nested / "renamed.bin"
    first.write_bytes(b"same payload")
    second.write_bytes(b"same payload")
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    manager = BackupJobManager(store, device_id="device-1")
    job = manager.create_job(
        [BackupSourceInput(str(first), "file"), BackupSourceInput(str(second), "file")],
        now="2026-06-08T03:00:00Z",
    )
    BackupScanner(store, device_id="device-1").scan_job(job.job.backup_job_id, now="2026-06-08T03:01:00Z")

    result = ContentDedupeIndexer(store, device_id="device-1").build_job_index(
        job.job.backup_job_id,
        now="2026-06-08T03:02:00Z",
    )

    assert result.content_object_count == 1
    assert result.reference_count == 2
    assert result.payload_source_count == 1
    assert result.local_duplicate_count == 1

    content_objects = store.list_content_objects()
    references = store.list_content_references(job.job.backup_job_id)
    assert len(content_objects) == 1
    assert len(references) == 2
    assert content_objects[0]["reference_count"] == 2
    assert content_objects[0]["payload_reference_count"] == 1
    assert {row["dedupe_status"] for row in references} == {"needs_payload", "local_duplicate"}

    with store.connect() as conn:
        outbox = conn.execute(
            """
            SELECT payload_json
            FROM sync_outbox
            WHERE entity_type = 'content_objects'
            """
        ).fetchone()
    assert outbox is not None
    assert '"content_id"' in outbox["payload_json"]
    assert '"file_sha256"' in outbox["payload_json"]
    assert '"size_bytes"' in outbox["payload_json"]
    assert str(first) not in outbox["payload_json"]
    assert str(second) not in outbox["payload_json"]
    assert '"local_path"' not in outbox["payload_json"]


def test_existing_content_in_another_job_makes_new_job_reference_local_duplicate(tmp_path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("same", encoding="utf-8")
    second.write_text("same", encoding="utf-8")
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    manager = BackupJobManager(store, device_id="device-1")
    first_job = manager.create_job([BackupSourceInput(str(first), "file")], now="2026-06-08T03:00:00Z")
    second_job = manager.create_job([BackupSourceInput(str(second), "file")], now="2026-06-08T03:10:00Z")
    scanner = BackupScanner(store, device_id="device-1")
    scanner.scan_job(first_job.job.backup_job_id, now="2026-06-08T03:01:00Z")
    scanner.scan_job(second_job.job.backup_job_id, now="2026-06-08T03:11:00Z")
    indexer = ContentDedupeIndexer(store, device_id="device-1")

    first_result = indexer.build_job_index(first_job.job.backup_job_id, now="2026-06-08T03:02:00Z")
    second_result = indexer.build_job_index(second_job.job.backup_job_id, now="2026-06-08T03:12:00Z")

    assert first_result.payload_source_count == 1
    assert second_result.payload_source_count == 0
    assert second_result.local_duplicate_count == 1
    assert store.list_content_objects()[0]["reference_count"] == 2
    assert store.list_content_objects()[0]["payload_reference_count"] == 1
    assert store.list_content_references(second_job.job.backup_job_id)[0]["dedupe_status"] == "local_duplicate"


def test_cloud_candidate_only_counts_when_sha256_and_size_match(tmp_path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("cloud candidate", encoding="utf-8")
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    manager = BackupJobManager(store, device_id="device-1")
    job = manager.create_job([BackupSourceInput(str(source), "file")], now="2026-06-08T03:00:00Z")
    BackupScanner(store, device_id="device-1").scan_job(job.job.backup_job_id, now="2026-06-08T03:01:00Z")
    indexer = ContentDedupeIndexer(store, device_id="device-1")
    indexer.build_job_index(job.job.backup_job_id, now="2026-06-08T03:02:00Z")
    local_content = store.list_content_objects()[0]

    result = indexer.refresh_cloud_candidates(
        job.job.backup_job_id,
        cloud_client=_FakeCloudContentClient(
            {
                local_content["content_id"]: ContentObject(
                    content_id=local_content["content_id"],
                    file_sha256=local_content["file_sha256"],
                    size_bytes=local_content["size_bytes"],
                    latest_entity_id="cloud-entity-1",
                    updated_at=datetime(2026, 6, 8, 3, 3, tzinfo=timezone.utc),
                )
            }
        ),
        now="2026-06-08T03:03:00Z",
    )

    assert result.cloud_duplicate_candidate_count == 1
    content = store.list_content_objects()[0]
    references = store.list_content_references(job.job.backup_job_id)
    assert content["cloud_candidate_status"] == "cloud_duplicate_candidate"
    assert content["cloud_latest_entity_id"] == "cloud-entity-1"
    assert content["payload_reference_count"] == 0
    assert content["duplicate_reference_count"] == 1
    assert references[0]["dedupe_status"] == "cloud_duplicate_candidate"


def test_cloud_candidate_hash_mismatch_does_not_mark_reference_duplicate(tmp_path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("local only", encoding="utf-8")
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    manager = BackupJobManager(store, device_id="device-1")
    job = manager.create_job([BackupSourceInput(str(source), "file")], now="2026-06-08T03:00:00Z")
    BackupScanner(store, device_id="device-1").scan_job(job.job.backup_job_id, now="2026-06-08T03:01:00Z")
    indexer = ContentDedupeIndexer(store, device_id="device-1")
    indexer.build_job_index(job.job.backup_job_id, now="2026-06-08T03:02:00Z")
    local_content = store.list_content_objects()[0]

    result = indexer.refresh_cloud_candidates(
        job.job.backup_job_id,
        cloud_client=_FakeCloudContentClient(
            {
                local_content["content_id"]: ContentObject(
                    content_id=local_content["content_id"],
                    file_sha256="f" * 64,
                    size_bytes=local_content["size_bytes"],
                    latest_entity_id="cloud-entity-1",
                    updated_at=datetime(2026, 6, 8, 3, 3, tzinfo=timezone.utc),
                )
            }
        ),
        now="2026-06-08T03:03:00Z",
    )

    assert result.hash_mismatch_count == 1
    assert store.list_content_objects()[0]["cloud_candidate_status"] == "hash_mismatch"
    assert store.list_content_objects()[0]["payload_reference_count"] == 1
    assert store.list_content_references(job.job.backup_job_id)[0]["dedupe_status"] == "needs_payload"


def test_rescan_removes_stale_references_and_decrements_old_content_count(tmp_path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("one", encoding="utf-8")
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    manager = BackupJobManager(store, device_id="device-1")
    job = manager.create_job([BackupSourceInput(str(source), "file")], now="2026-06-08T03:00:00Z")
    scanner = BackupScanner(store, device_id="device-1")
    indexer = ContentDedupeIndexer(store, device_id="device-1")
    scanner.scan_job(job.job.backup_job_id, now="2026-06-08T03:01:00Z")
    indexer.build_job_index(job.job.backup_job_id, now="2026-06-08T03:02:00Z")
    old_content_id = store.list_content_objects()[0]["content_id"]

    source.write_text("two", encoding="utf-8")
    scanner.scan_job(job.job.backup_job_id, now="2026-06-08T03:03:00Z")
    indexer.build_job_index(job.job.backup_job_id, now="2026-06-08T03:04:00Z")

    objects = {row["content_id"]: row for row in store.list_content_objects()}
    assert objects[old_content_id]["reference_count"] == 0
    assert objects[old_content_id]["payload_reference_count"] == 0
    assert sum(1 for row in objects.values() if row["reference_count"] == 1) == 1
    assert len(store.list_content_references(job.job.backup_job_id)) == 1


def test_content_id_collision_is_rejected_before_index_write(tmp_path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("payload", encoding="utf-8")
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    manager = BackupJobManager(store, device_id="device-1")
    job = manager.create_job([BackupSourceInput(str(source), "file")], now="2026-06-08T03:00:00Z")
    BackupScanner(store, device_id="device-1").scan_job(job.job.backup_job_id, now="2026-06-08T03:01:00Z")
    with store.transaction() as conn:
        row = conn.execute("SELECT file_item_id, size_bytes FROM file_items").fetchone()
        wrong_content_id = file_content_id(int(row["size_bytes"]) + 1, "a" * 64)
        conn.execute(
            """
            UPDATE file_items
            SET sha256 = ?, content_id = ?
            WHERE file_item_id = ?
            """,
            ("a" * 64, wrong_content_id, row["file_item_id"]),
        )

    with pytest.raises(DedupeIndexError, match="content_id does not match"):
        ContentDedupeIndexer(store, device_id="device-1").build_job_index(job.job.backup_job_id)

    assert store.list_content_objects() == []


class _FakeCloudContentClient:
    def __init__(self, objects: dict[str, ContentObject], *, status_code: int = 404) -> None:
        self.objects = objects
        self.status_code = status_code

    def get_content(self, content_id: str) -> ContentObject:
        if content_id in self.objects:
            return self.objects[content_id]
        raise CloudAPIError(self.status_code, "not_found", "content object not found")
