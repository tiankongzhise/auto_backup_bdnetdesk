from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from auto_backup_client.backup_jobs import BackupJobManager, BackupSourceInput
from auto_backup_client.scan_fingerprints import (
    FILE_ATTRIBUTE_REPARSE_POINT,
    BackupScanner,
    FileFingerprint,
    file_content_id,
    fingerprint_file,
    folder_content_hash,
    folder_manifest_hash,
    quick_sample_ranges,
    skip_issue_type_from_metadata,
)
from auto_backup_client.sqlite_store import SQLiteClientStore


def test_file_fingerprints_are_content_based_and_ignore_path_name_and_time(tmp_path) -> None:
    first = tmp_path / "a.txt"
    second_dir = tmp_path / "nested"
    second_dir.mkdir()
    second = second_dir / "renamed.bin"
    first.write_bytes(b"same bytes")
    second.write_bytes(b"same bytes")
    shifted_time = int(time.time()) - 3600
    os.utime(second, (shifted_time, shifted_time))

    first_fp = fingerprint_file(first)
    second_fp = fingerprint_file(second)

    assert first_fp.quick_fingerprint == second_fp.quick_fingerprint
    assert first_fp.md5 == second_fp.md5 == hashlib.md5(b"same bytes").hexdigest()
    assert first_fp.sha256 == second_fp.sha256 == hashlib.sha256(b"same bytes").hexdigest()
    assert first_fp.content_id == second_fp.content_id == file_content_id(len(b"same bytes"), first_fp.sha256)


def test_quick_sample_ranges_follow_dynamic_sampling_boundaries() -> None:
    assert len(quick_sample_ranges(16 * 1024 * 1024)) == 1
    assert len(quick_sample_ranges(17 * 1024 * 1024)) == 4
    assert len(quick_sample_ranges(300 * 1024 * 1024)) == 8

    large_ranges = quick_sample_ranges(9 * 1024 * 1024 * 1024)
    assert len(large_ranges) == 32
    assert large_ranges[0].offset == 0
    assert large_ranges[-1].offset % 4096 == 0


def test_folder_content_hash_ignores_paths_but_manifest_hash_tracks_structure() -> None:
    content_id = "a" * 64
    same_content_a = folder_content_hash([("file", content_id)])
    same_content_b = folder_content_hash([("file", content_id)])
    manifest_a = folder_manifest_hash(
        [
            {
                "type": "file",
                "relative_path": "one/name.txt",
                "name": "name.txt",
                "size": 1,
                "ctime_ns": 1,
                "mtime_ns": 1,
                "atime_ns": 1,
                "attrs": 0,
                "content_id": content_id,
            }
        ]
    )
    manifest_b = folder_manifest_hash(
        [
            {
                "type": "file",
                "relative_path": "two/renamed.txt",
                "name": "renamed.txt",
                "size": 1,
                "ctime_ns": 1,
                "mtime_ns": 1,
                "atime_ns": 1,
                "attrs": 0,
                "content_id": content_id,
            }
        ]
    )

    assert same_content_a == same_content_b
    assert manifest_a != manifest_b


def test_scan_job_persists_file_items_folders_and_outbox_without_local_paths(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    nested = root / "nested"
    nested.mkdir()
    keep_file = nested / "keep.txt"
    keep_file.write_text("payload", encoding="utf-8")
    same_bytes_elsewhere = tmp_path / "other-name.txt"
    same_bytes_elsewhere.write_text("payload", encoding="utf-8")
    shortcut = root / "skip.lnk"
    shortcut.write_text("not scanned", encoding="utf-8")
    if hasattr(os, "symlink"):
        try:
            os.symlink(keep_file, root / "skip-symlink.txt")
        except OSError:
            pass

    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    manager = BackupJobManager(store, device_id="device-1")
    job = manager.create_job(
        [BackupSourceInput(str(root), "directory"), BackupSourceInput(str(same_bytes_elsewhere), "file")],
        now="2026-06-08T02:00:00Z",
    )

    scanner = BackupScanner(store, device_id="device-1")
    result = scanner.scan_job(job.job.backup_job_id, now="2026-06-08T02:01:00Z")

    assert result.file_count == 2
    assert result.folder_count == 2
    assert result.issue_count >= 1

    files = store.list_file_items(job.job.backup_job_id)
    folders = store.list_folder_items(job.job.backup_job_id)
    issues = store.list_scan_issues(job.job.backup_job_id)

    assert {row["relative_path"] for row in files} == {"nested/keep.txt", "other-name.txt"}
    assert {row["relative_path"] for row in folders} == {"", "nested"}
    assert any(row["issue_type"] == "skipped_shortcut" for row in issues)
    assert len({row["content_id"] for row in files}) == 1

    with store.connect() as conn:
        outbox_rows = conn.execute("SELECT entity_type, payload_json FROM sync_outbox ORDER BY created_at").fetchall()
    file_and_folder_events = [row for row in outbox_rows if row["entity_type"] in {"file_items", "folder_items"}]
    assert len(file_and_folder_events) == len(files) + len(folders)
    payload_json = "\n".join(row["payload_json"] for row in file_and_folder_events)
    assert str(root) not in payload_json
    assert str(keep_file) not in payload_json
    assert str(same_bytes_elsewhere) not in payload_json
    assert '"local_path"' not in payload_json


def test_scan_records_unreadable_file_and_continues_with_other_files(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    good = root / "good.txt"
    bad = root / "bad.txt"
    good.write_text("good", encoding="utf-8")
    bad.write_text("bad", encoding="utf-8")
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    manager = BackupJobManager(store, device_id="device-1")
    job = manager.create_job([BackupSourceInput(str(root), "directory")], now="2026-06-08T02:00:00Z")

    def fake_fingerprint(path: Path) -> FileFingerprint:
        if path.name == "bad.txt":
            raise PermissionError("denied")
        return fingerprint_file(path)

    scanner = BackupScanner(store, device_id="device-1", fingerprint_file_func=fake_fingerprint)
    result = scanner.scan_job(job.job.backup_job_id, now="2026-06-08T02:01:00Z")

    assert result.file_count == 1
    assert result.issue_count == 1
    assert [row["display_name"] for row in store.list_file_items(job.job.backup_job_id)] == ["good.txt"]
    assert store.list_scan_issues(job.job.backup_job_id)[0]["issue_type"] == "unreadable_file"


def test_rescan_replaces_current_scan_rows_and_increments_item_versions(tmp_path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("one", encoding="utf-8")
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    manager = BackupJobManager(store, device_id="device-1")
    job = manager.create_job([BackupSourceInput(str(source), "file")], now="2026-06-08T02:00:00Z")
    scanner = BackupScanner(store, device_id="device-1")

    scanner.scan_job(job.job.backup_job_id, now="2026-06-08T02:01:00Z")
    first_file = store.list_file_items(job.job.backup_job_id)[0]
    source.write_text("two", encoding="utf-8")
    scanner.scan_job(job.job.backup_job_id, now="2026-06-08T02:02:00Z")
    second_file = store.list_file_items(job.job.backup_job_id)[0]

    assert first_file["file_item_id"] == second_file["file_item_id"]
    assert second_file["data_version"] == first_file["data_version"] + 1
    assert second_file["sha256"] != first_file["sha256"]
    with store.connect() as conn:
        events = conn.execute(
            """
            SELECT payload_json
            FROM sync_outbox
            WHERE entity_type = 'file_items'
            ORDER BY created_at
            """
        ).fetchall()
    assert [json.loads(row["payload_json"])["data_version"] for row in events] == [1, 2]


def test_skip_issue_type_classifies_links_shortcuts_and_reparse_points() -> None:
    assert skip_issue_type_from_metadata(suffix=".lnk", is_symlink=False, file_attrs=0) == "skipped_shortcut"
    assert skip_issue_type_from_metadata(suffix=".txt", is_symlink=True, file_attrs=0) == "skipped_symlink"
    assert (
        skip_issue_type_from_metadata(
            suffix=".txt",
            is_symlink=False,
            file_attrs=FILE_ATTRIBUTE_REPARSE_POINT,
        )
        == "skipped_junction"
    )
    assert skip_issue_type_from_metadata(suffix=".txt", is_symlink=False, file_attrs=0) is None
