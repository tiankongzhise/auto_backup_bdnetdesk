from __future__ import annotations

from auto_backup_client.archive_packager import ArchivePackager
from auto_backup_client.backup_jobs import BackupJobManager, BackupSourceInput
from auto_backup_client.dedupe_index import ContentDedupeIndexer
from auto_backup_client.scan_fingerprints import BackupScanner
from auto_backup_client.source_mapping import SourceMappingQuery, path_digest
from auto_backup_client.sqlite_store import SQLiteClientStore, build_version_fields


TEST_PASSWORD = "Test123456789"
NOW = "2026-06-08T18:00:00Z"


def test_source_mapping_links_sources_content_archives_and_remote_objects(tmp_path) -> None:
    first = tmp_path / "private" / "alpha.txt"
    first.parent.mkdir()
    second = tmp_path / "private" / "copy.txt"
    first.write_text("same secret payload", encoding="utf-8")
    second.write_text("same secret payload", encoding="utf-8")
    store, job_id = _packaged_job(tmp_path, [first, second])
    archive = store.list_archives(job_id)[0]
    archive_path = f"/apps/app/backups/2026/06/08/device-1/{job_id}/archives/000001-{archive['archive_sha256']}.7z"
    meta_path = archive_path.replace(".7z", ".meta.json")
    index_path = f"/apps/app/backups/2026/06/08/device-1/{job_id}/job.index.json"
    _insert_remote_object(store, archive, object_type="archive", remote_path=archive_path, status="remote_created", fs_id=111)
    _insert_remote_object(store, archive, object_type="archive_meta", remote_path=meta_path, status="remote_created", fs_id=112)
    _insert_remote_object(store, archive, object_type="job_index", remote_path=index_path, status="remote_created", fs_id=113)

    report = SourceMappingQuery(store).list_rows(backup_job_id=job_id)

    assert report.summary.total_rows == 2
    assert report.summary.job_count == 1
    assert report.summary.source_count == 2
    assert report.summary.content_count == 1
    assert report.summary.archive_count == 1
    assert report.summary.remote_object_count == 3
    assert report.summary.baidu_ready_count == 2
    assert {row.dedupe_status for row in report.rows} == {"archive_assigned"}
    assert {row.reference_role for row in report.rows} == {"payload_source", "local_duplicate"}
    assert all(row.remote_archive_path_sha256 == path_digest(archive_path) for row in report.rows)
    assert str(first) not in {row.display_name for row in report.rows}
    assert all(str(tmp_path) not in row.remote_archive_path_sha256 for row in report.rows)


def test_source_mapping_keyword_filter_and_unuploaded_state(tmp_path) -> None:
    source = tmp_path / "lonely.txt"
    source.write_text("local only", encoding="utf-8")
    store, job_id = _indexed_job(tmp_path, [source])

    report = SourceMappingQuery(store).list_rows(keyword="lonely")

    assert report.summary.total_rows == 1
    row = report.rows[0]
    assert row.backup_job_id == job_id
    assert row.archive_type == "not_packaged"
    assert row.remote_archive_status == "not_uploaded"
    assert row.baidu_ready is False


def _indexed_job(tmp_path, sources):  # type: ignore[no-untyped-def]
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    job = BackupJobManager(store, device_id="device-1").create_job(
        [BackupSourceInput(str(source), "file") for source in sources],
        job_name="mapping job",
        now=NOW,
    )
    BackupScanner(store, device_id="device-1").scan_job(job.job.backup_job_id, now="2026-06-08T18:01:00Z")
    ContentDedupeIndexer(store, device_id="device-1").build_job_index(job.job.backup_job_id, now="2026-06-08T18:02:00Z")
    return store, job.job.backup_job_id


def _packaged_job(tmp_path, sources):  # type: ignore[no-untyped-def]
    store, job_id = _indexed_job(tmp_path, sources)
    ArchivePackager(store, device_id="device-1").package_job(
        job_id,
        cache_root=tmp_path / "cache",
        password=TEST_PASSWORD,
        now="2026-06-08T18:03:00Z",
    )
    return store, job_id


def _insert_remote_object(store: SQLiteClientStore, archive: dict[str, object], *, object_type: str, remote_path: str, status: str, fs_id: int) -> None:
    with store.transaction() as conn:
        payload = build_version_fields(
            entity_payload={
                "remote_object_id": f"remote-{object_type}",
                "entity_id": f"remote_object_{object_type}",
                "object_type": object_type,
                "job_id": archive["job_id"],
                "device_id": archive["device_id"],
                "archive_id": archive["archive_id"] if object_type != "job_index" else "",
                "archive_sha256": archive["archive_sha256"] if object_type != "job_index" else "",
                "remote_path": remote_path,
                "size_bytes": archive["archive_size"],
                "md5": archive["archive_md5"],
                "sha256": archive["archive_sha256"],
                "fs_id": fs_id,
                "status": status,
                "created_at": "2026-06-08T18:04:00Z",
            },
            updated_by_device_id="device-1",
            now="2026-06-08T18:04:00Z",
        )
        store.put_remote_object(conn, payload)
