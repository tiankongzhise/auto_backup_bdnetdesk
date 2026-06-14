from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from auto_backup_client import archive_packager
from auto_backup_client.archive_packager import (
    ArchivePackager,
    ArchivePackagingError,
    SevenZipRunner,
    file_sha256,
    resolve_7zip_executable,
)
from auto_backup_client.backup_jobs import BackupJobManager, BackupSourceInput
from auto_backup_client.dedupe_index import ContentDedupeIndexer
from auto_backup_client.scan_fingerprints import BackupScanner
from auto_backup_client.sqlite_store import SQLiteClientStore


TEST_ARCHIVE_PASSWORD = "Test123456789"


def test_package_job_creates_real_encrypted_7z_manifest_and_cleans_plain_manifest(tmp_path) -> None:
    source = tmp_path / "source.txt"
    duplicate = tmp_path / "duplicate.txt"
    source.write_text("same bytes", encoding="utf-8")
    duplicate.write_text("same bytes", encoding="utf-8")
    store, job_id = _indexed_job(tmp_path, [source, duplicate])

    result = ArchivePackager(store, device_id="device-1").package_job(
        job_id,
        cache_root=tmp_path / "cache",
        password=TEST_ARCHIVE_PASSWORD,
        now="2026-06-08T08:00:00Z",
    )

    assert result.archive_path.is_file()
    assert result.archive_sha256 == file_sha256(result.archive_path)
    assert result.archive_type == "mixed"
    assert result.payload_member_count == 1
    assert result.reference_member_count == 1
    assert not (tmp_path / "cache" / "jobs" / job_id / "manifest_plain").exists()
    assert not (tmp_path / "cache" / "jobs" / job_id / "tmp" / "archive_000001").exists()
    assert not (tmp_path / "cache" / "jobs" / job_id / "verify" / "archive_000001").exists()

    archive = store.list_archives(job_id)[0]
    members = store.list_archive_members(result.archive_id)
    references = store.list_content_references(job_id)
    assert archive["verify_status"] == "standard_test_passed"
    assert archive["archive_sha256"] == result.archive_sha256
    assert {row["member_type"] for row in members} == {"manifest", "payload", "reference"}
    assert {row["dedupe_status"] for row in references} == {"archive_assigned"}
    assert all(row["archive_id"] == result.archive_id for row in references)
    assert all(row["archive_sha256"] == result.archive_sha256 for row in references)

    extracted_manifest = _extract_manifest(tmp_path, result.archive_path)
    manifest_text = extracted_manifest.read_text(encoding="utf-8")
    assert result.manifest_sha256 == file_sha256(extracted_manifest)
    assert "Test123456789" not in manifest_text
    assert '"original_path"' in manifest_text
    assert '"payload/' in manifest_text

    with store.connect() as conn:
        outbox = conn.execute(
            """
            SELECT payload_json
            FROM sync_outbox
            WHERE entity_type = 'archives'
            """
        ).fetchone()
    assert outbox is not None
    assert str(result.archive_path) not in outbox["payload_json"]
    assert "Test123456789" not in outbox["payload_json"]
    assert '"local_archive_path"' not in outbox["payload_json"]


def test_manifest_only_archive_is_created_when_all_references_are_local_duplicates(tmp_path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("same", encoding="utf-8")
    second.write_text("same", encoding="utf-8")
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    manager = BackupJobManager(store, device_id="device-1")
    first_job = manager.create_job([BackupSourceInput(str(first), "file")], now="2026-06-08T07:00:00Z")
    second_job = manager.create_job([BackupSourceInput(str(second), "file")], now="2026-06-08T07:10:00Z")
    scanner = BackupScanner(store, device_id="device-1")
    scanner.scan_job(first_job.job.backup_job_id, now="2026-06-08T07:01:00Z")
    scanner.scan_job(second_job.job.backup_job_id, now="2026-06-08T07:11:00Z")
    indexer = ContentDedupeIndexer(store, device_id="device-1")
    indexer.build_job_index(first_job.job.backup_job_id, now="2026-06-08T07:02:00Z")
    ArchivePackager(store, device_id="device-1").package_job(
        first_job.job.backup_job_id,
        cache_root=tmp_path / "cache",
        password=TEST_ARCHIVE_PASSWORD,
        now="2026-06-08T07:03:00Z",
    )
    indexer.build_job_index(second_job.job.backup_job_id, now="2026-06-08T07:12:00Z")

    result = ArchivePackager(store, device_id="device-1").package_job(
        second_job.job.backup_job_id,
        cache_root=tmp_path / "cache",
        password=TEST_ARCHIVE_PASSWORD,
        now="2026-06-08T08:00:00Z",
    )

    assert result.archive_type == "manifest_only"
    assert result.payload_member_count == 0
    assert result.reference_member_count == 1
    members = store.list_archive_members(result.archive_id)
    assert {row["member_type"] for row in members} == {"manifest", "reference"}
    extracted_manifest = _extract_manifest(tmp_path, result.archive_path)
    assert '"payload/' in extracted_manifest.read_text(encoding="utf-8")


def test_packaging_rejects_changed_payload_source_before_archive_write(tmp_path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("before", encoding="utf-8")
    store, job_id = _indexed_job(tmp_path, [source])
    source.write_text("after", encoding="utf-8")

    with pytest.raises(ArchivePackagingError, match="changed after scan"):
        ArchivePackager(store, device_id="device-1").package_job(
            job_id,
            cache_root=tmp_path / "cache",
            password=TEST_ARCHIVE_PASSWORD,
            now="2026-06-08T08:00:00Z",
        )

    assert store.list_archives(job_id) == []


def test_package_job_handles_same_stem_directory_and_zip_sources_on_retry(tmp_path) -> None:
    folder = tmp_path / "LibreHardwareMonitor"
    folder.mkdir()
    (folder / "LibreHardwareMonitor.exe").write_bytes(b"exe")
    (folder / "LibreHardwareMonitor.config").write_text("config", encoding="utf-8")
    zip_file = tmp_path / "LibreHardwareMonitor.zip"
    zip_file.write_bytes(b"zip payload")
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    job = BackupJobManager(store, device_id="device-1").create_job(
        [BackupSourceInput(str(folder), "directory"), BackupSourceInput(str(zip_file), "file")],
        now="2026-06-08T06:00:00Z",
    )
    BackupScanner(store, device_id="device-1").scan_job(job.job.backup_job_id, now="2026-06-08T06:01:00Z")
    ContentDedupeIndexer(store, device_id="device-1").build_job_index(
        job.job.backup_job_id,
        now="2026-06-08T06:02:00Z",
    )
    packager = ArchivePackager(store, device_id="device-1")

    first = packager.package_job(
        job.job.backup_job_id,
        cache_root=tmp_path / "cache",
        password=TEST_ARCHIVE_PASSWORD,
        archive_seq=1,
        backup_source_id=job.sources[0].backup_source_id,
        now="2026-06-08T08:00:00Z",
    )
    second = packager.package_job(
        job.job.backup_job_id,
        cache_root=tmp_path / "cache",
        password=TEST_ARCHIVE_PASSWORD,
        archive_seq=2,
        backup_source_id=job.sources[1].backup_source_id,
        now="2026-06-08T08:00:01Z",
    )
    retry_first = packager.package_job(
        job.job.backup_job_id,
        cache_root=tmp_path / "cache",
        password=TEST_ARCHIVE_PASSWORD,
        archive_seq=1,
        backup_source_id=job.sources[0].backup_source_id,
        now="2026-06-08T08:00:02Z",
    )

    assert first.archive_path.is_file()
    assert second.archive_path.is_file()
    assert retry_first.archive_path.is_file()
    archives = store.list_archives(job.job.backup_job_id)
    assert [row["archive_seq"] for row in archives] == [1, 2]
    assert all(row["verify_status"] == "standard_test_passed" for row in archives)
    assert not any(row["archive_id"] == row["referenced_archive_id"] for row in store.list_archive_members(retry_first.archive_id))


def test_seven_zip_error_summary_redacts_paths_and_password(tmp_path, monkeypatch) -> None:
    class FailingRunner(SevenZipRunner):
        def __init__(self) -> None:
            self.executable = Path("7z.exe")

    def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        return SimpleNamespace(
            returncode=2,
            stderr=(
                "ERROR: C:\\Users\\3700x\\Downloads\\secret.txt\n"
                "-pSuperSecret\n"
                "-oC:\\Users\\3700x\\Desktop\\新建文件夹\\cache"
            ),
            stdout="Cannot open file",
        )

    monkeypatch.setattr(archive_packager.subprocess, "run", fake_run)
    runner = FailingRunner()

    with pytest.raises(ArchivePackagingError) as raised:
        runner._run(
            "add",
            ["7z.exe", "a"],
            cwd=tmp_path,
        )

    message = str(raised.value)
    assert "7-Zip add failed with exit code 2" in message
    assert "Cannot open file" in message
    assert "[redacted-path]" in message
    assert "SuperSecret" not in message
    assert "C:\\Users" not in message


def _indexed_job(tmp_path: Path, sources: list[Path]) -> tuple[SQLiteClientStore, str]:
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    job = BackupJobManager(store, device_id="device-1").create_job(
        [BackupSourceInput(str(source), "file") for source in sources],
        now="2026-06-08T06:00:00Z",
    )
    BackupScanner(store, device_id="device-1").scan_job(job.job.backup_job_id, now="2026-06-08T06:01:00Z")
    ContentDedupeIndexer(store, device_id="device-1").build_job_index(
        job.job.backup_job_id,
        now="2026-06-08T06:02:00Z",
    )
    return store, job.job.backup_job_id


def _extract_manifest(tmp_path: Path, archive_path: Path) -> Path:
    output_dir = tmp_path / "manual_extract" / archive_path.stem
    output_dir.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            str(resolve_7zip_executable()),
            "x",
            "-y",
            f"-p{TEST_ARCHIVE_PASSWORD}",
            str(archive_path),
            "manifest/manifest.json",
            f"-o{output_dir}",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert completed.returncode == 0, completed.stderr
    manifest = output_dir / "manifest" / "manifest.json"
    assert manifest.is_file()
    return manifest
