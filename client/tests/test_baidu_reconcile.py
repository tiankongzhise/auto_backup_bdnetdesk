from __future__ import annotations

from auto_backup_client.baidu.reconcile import (
    STATUS_BAIDU_ONLY,
    STATUS_CONSISTENT,
    STATUS_DB_EXISTS_REMOTE_MISSING,
    STATUS_FS_ID_CHANGED,
    STATUS_REMOTE_META_MISMATCH,
    STATUS_REMOTE_META_MISSING,
    STATUS_REMOTE_SIZE_MISMATCH,
    STATUS_REMOTE_UNREADABLE,
    RemoteObjectReconciler,
    RemoteReconcileScope,
    RequestRateLimiter,
)
from auto_backup_client.baidu.upload import BaiduFileItem, BaiduFileListResult, BaiduNetdiskError
from auto_backup_client.sqlite_store import SQLiteClientStore, build_version_fields


NOW = "2026-06-08T00:00:00Z"
JOB_DIR = "/apps/auto_backup_bdnetdesk/backups/2026/06/08/device-secret/job-secret"


class FakeBaidu:
    def __init__(self, pages: dict[tuple[str, int], BaiduFileListResult], *, unreadable: set[str] | None = None) -> None:
        self.pages = pages
        self.unreadable = unreadable or set()
        self.list_all_calls: list[tuple[str, int, int, bool]] = []
        self.list_dir_calls: list[str] = []

    def list_all(self, *, remote_path: str, start: int = 0, limit: int = 1000, recursion: bool = True, web: bool = False):
        del web
        self.list_all_calls.append((remote_path, start, limit, recursion))
        if remote_path in self.unreadable:
            raise BaiduNetdiskError("unreadable", error_code="-9")
        return self.pages.get((remote_path, start), BaiduFileListResult(errno=0, items=tuple()))

    def list_dir(self, *, remote_dir: str, limit: int = 1000, **_kwargs: object):
        self.list_dir_calls.append(remote_dir)
        if remote_dir in self.unreadable:
            raise BaiduNetdiskError("unreadable", error_code="-9")
        return self.pages.get((remote_dir, 0), BaiduFileListResult(errno=0, items=tuple()))


def test_reconcile_reports_all_minimal_difference_statuses(tmp_path) -> None:
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    _put_remote_object(store, "consistent", "archive", _path("consistent.7z"), size=10, md5="a" * 32, fs_id=1)
    _put_remote_object(store, "missing", "archive", _path("missing.7z"), size=20, md5="b" * 32, fs_id=2)
    _put_remote_object(store, "meta-missing", "archive_meta", _path("missing.meta.json"), size=30, md5="c" * 32, fs_id=3)
    _put_remote_object(store, "meta-mismatch", "archive_meta", _path("mismatch.meta.json"), size=40, md5="d" * 32, fs_id=4)
    _put_remote_object(store, "size", "archive", _path("size.7z"), size=50, md5="e" * 32, fs_id=5)
    _put_remote_object(store, "fs", "archive", _path("fs.7z"), size=60, md5="f" * 32, fs_id=6)

    baidu = FakeBaidu(
        {
            (JOB_DIR, 0): BaiduFileListResult(
                errno=0,
                items=(
                    _item(_path("consistent.7z"), size=10, md5="a" * 32, fs_id=1),
                    _item(_path("mismatch.meta.json"), size=41, md5="d" * 32, fs_id=4),
                    _item(_path("size.7z"), size=51, md5="e" * 32, fs_id=5),
                    _item(_path("fs.7z"), size=60, md5="f" * 32, fs_id=7),
                    _item(_path("baidu-only.7z"), size=70, md5="1" * 32, fs_id=8),
                ),
            )
        }
    )

    report = RemoteObjectReconciler(
        store=store,
        baidu=baidu,
        rate_limiter=RequestRateLimiter(sleeper=lambda _seconds: None),
    ).reconcile(RemoteReconcileScope(job_id="job-secret"))

    assert report.local_object_count == 6
    assert report.remote_object_count == 5
    assert report.status_counts[STATUS_CONSISTENT] == 1
    assert report.status_counts[STATUS_DB_EXISTS_REMOTE_MISSING] == 1
    assert report.status_counts[STATUS_REMOTE_META_MISSING] == 1
    assert report.status_counts[STATUS_REMOTE_META_MISMATCH] == 1
    assert report.status_counts[STATUS_REMOTE_SIZE_MISMATCH] == 1
    assert report.status_counts[STATUS_FS_ID_CHANGED] == 1
    assert report.status_counts[STATUS_BAIDU_ONLY] == 1
    assert baidu.list_all_calls == [(JOB_DIR, 0, 1000, True)]


def test_reconcile_marks_local_objects_unreadable_when_baidu_list_fails(tmp_path) -> None:
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    _put_remote_object(store, "archive", "archive", _path("archive.7z"), size=10, md5="a" * 32, fs_id=1)

    report = RemoteObjectReconciler(
        store=store,
        baidu=FakeBaidu({}, unreadable={JOB_DIR}),
        rate_limiter=RequestRateLimiter(sleeper=lambda _seconds: None),
    ).reconcile(RemoteReconcileScope(job_id="job-secret"))

    assert report.status_counts[STATUS_REMOTE_UNREADABLE] == 1
    assert report.findings[0].error_code == "remote_unreadable"


def test_reconcile_uses_listall_pagination_and_injected_rate_limiter(tmp_path) -> None:
    sleeps: list[float] = []
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    baidu = FakeBaidu(
        {
            (JOB_DIR, 0): BaiduFileListResult(
                errno=0,
                items=(_item(_path("first.7z"), size=1, md5="a" * 32, fs_id=1),),
                has_more=True,
                cursor=20,
            ),
            (JOB_DIR, 20): BaiduFileListResult(
                errno=0,
                items=(_item(_path("second.7z"), size=2, md5="b" * 32, fs_id=2),),
            ),
        }
    )

    report = RemoteObjectReconciler(
        store=store,
        baidu=baidu,
        rate_limiter=RequestRateLimiter(max_requests_per_minute=8, sleeper=sleeps.append),
    ).reconcile(RemoteReconcileScope(remote_dir=JOB_DIR, page_limit=1))

    assert [call[1] for call in baidu.list_all_calls] == [0, 20]
    assert sleeps == [7.5]
    assert report.status_counts[STATUS_BAIDU_ONLY] == 2


def test_reconcile_can_use_non_recursive_list_dir(tmp_path) -> None:
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    baidu = FakeBaidu({(JOB_DIR, 0): BaiduFileListResult(errno=0, items=(_item(_path("only.7z"), size=1, md5="a" * 32, fs_id=1),))})

    report = RemoteObjectReconciler(
        store=store,
        baidu=baidu,
        rate_limiter=RequestRateLimiter(sleeper=lambda _seconds: None),
    ).reconcile(RemoteReconcileScope(remote_dir=JOB_DIR, recursive=False))

    assert baidu.list_dir_calls == [JOB_DIR]
    assert report.status_counts[STATUS_BAIDU_ONLY] == 1


def test_reconcile_uses_upload_session_paths_when_remote_objects_are_absent(tmp_path) -> None:
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    _put_upload_session(store)

    report = RemoteObjectReconciler(
        store=store,
        baidu=FakeBaidu({(JOB_DIR, 0): BaiduFileListResult(errno=0, items=tuple())}),
        rate_limiter=RequestRateLimiter(sleeper=lambda _seconds: None),
    ).reconcile(RemoteReconcileScope(upload_session_id="session-secret"))

    statuses_by_type = {finding.object_type: finding.status for finding in report.findings}
    assert statuses_by_type == {
        "archive": STATUS_DB_EXISTS_REMOTE_MISSING,
        "archive_meta": STATUS_REMOTE_META_MISSING,
        "job_index": STATUS_REMOTE_META_MISSING,
    }


def _put_remote_object(store: SQLiteClientStore, suffix: str, object_type: str, remote_path: str, *, size: int, md5: str, fs_id: int) -> None:
    with store.transaction() as conn:
        payload = build_version_fields(
            entity_payload={
                "remote_object_id": f"remote-{suffix}",
                "entity_id": f"remote_object_{suffix}",
                "object_type": object_type,
                "job_id": "job-secret",
                "device_id": "device-secret",
                "archive_id": f"archive-{suffix}",
                "archive_sha256": "a" * 64,
                "remote_path": remote_path,
                "size_bytes": size,
                "md5": md5,
                "sha256": "a" * 64,
                "fs_id": fs_id,
                "status": "remote_created",
                "created_at": NOW,
            },
            updated_by_device_id="device-secret",
            now=NOW,
            revision_id=f"rev-{suffix}",
        )
        store.put_remote_object(conn, payload)


def _put_upload_session(store: SQLiteClientStore) -> None:
    with store.transaction() as conn:
        payload = build_version_fields(
            entity_payload={
                "upload_session_id": "session-secret",
                "entity_id": "upload_session_secret",
                "job_id": "job-secret",
                "device_id": "device-secret",
                "account_id": "account-secret",
                "archive_id": "archive-secret",
                "archive_seq": 1,
                "archive_sha256": "a" * 64,
                "archive_md5": "b" * 32,
                "archive_size": 10,
                "archive_type": "payload",
                "local_archive_path": "C:/sensitive/archive.7z",
                "remote_archive_path": _path("archive.7z"),
                "remote_meta_path": _path("archive.meta.json"),
                "remote_job_index_path": f"{JOB_DIR}/job.index.json",
                "part_size": 4 * 1024 * 1024,
                "total_parts": 1,
                "block_md5s_json": '["' + ("b" * 32) + '"]',
                "uploadid": "secret-uploadid",
                "upload_status": "remote_created",
                "meta_status": "uploaded",
                "job_index_status": "uploaded",
                "fs_id": 101,
                "remote_md5": "b" * 32,
                "error_code": "",
                "error_message": "",
                "completed_at": NOW,
                "created_at": NOW,
            },
            updated_by_device_id="device-secret",
            now=NOW,
            revision_id="rev-session",
        )
        store.put_upload_session(conn, payload)


def _path(name: str) -> str:
    return f"{JOB_DIR}/archives/{name}" if name != "job.index.json" else f"{JOB_DIR}/job.index.json"


def _item(remote_path: str, *, size: int, md5: str, fs_id: int) -> BaiduFileItem:
    return BaiduFileItem(fs_id=fs_id, path=remote_path, server_filename=remote_path.rsplit("/", 1)[-1], isdir=False, size=size, md5=md5)
