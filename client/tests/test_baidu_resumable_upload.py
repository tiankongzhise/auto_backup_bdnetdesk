from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace

from auto_backup_client.baidu.metadata import (
    ArchiveMetaInput,
    JobIndexArchive,
    build_archive_meta_document,
    build_job_index_document,
)
from auto_backup_client.baidu.resumable_upload import BaiduResumableUploader, ResumableArchiveInput
from auto_backup_client.baidu.upload import (
    DEFAULT_PART_SIZE,
    BaiduNetdiskError,
    CreateFileResult,
    FileManagerResult,
    LocateUploadResult,
    PrecreateResult,
    UploadPartResult,
)
from auto_backup_client.sqlite_store import SQLiteClientStore


class FakeBaiduClient:
    def __init__(self, *, expire_uploadid: bool = False, block_list: tuple[int, ...] | None = None) -> None:
        self.expire_uploadid = expire_uploadid
        self.block_list = block_list
        self.precreate_uploadids: list[str] = []
        self.uploaded_partseqs: list[int] = []
        self.complete_upload_paths: list[str] = []
        self.deleted_paths: list[str] = []
        self.uploaded_documents: dict[str, bytes] = {}

    def precreate(self, **kwargs):
        uploadid = kwargs.get("uploadid", "")
        self.precreate_uploadids.append(uploadid)
        if self.expire_uploadid and uploadid:
            raise BaiduNetdiskError("expired", error_code="invalid_uploadid")
        block_list = self.block_list
        if block_list is None:
            block_list = (1,) if len(kwargs["block_md5s"]) > 1 else (0,)
        return PrecreateResult(
            path=kwargs["remote_path"],
            uploadid=uploadid or "fresh-uploadid",
            return_type=1,
            block_list=block_list,
        )

    def locate_upload_server(self, **_kwargs):
        return LocateUploadResult(upload_server="https://upload.example.test", servers=("https://upload.example.test",))

    def upload_part(self, **kwargs):
        partseq = kwargs["partseq"]
        self.uploaded_partseqs.append(partseq)
        part = kwargs["plan"].part_by_seq(partseq)
        return UploadPartResult(partseq=partseq, md5=part.md5)

    def create_file(self, **kwargs):
        return CreateFileResult(
            fs_id=100 + len(self.complete_upload_paths),
            path=kwargs["remote_path"],
            md5="c" * 32,
            server_filename=kwargs["remote_path"].rsplit("/", 1)[-1],
        )

    def upload_file_complete(self, *, local_path, remote_path: str, part_size: int, rtype: int):
        del part_size, rtype
        self.complete_upload_paths.append(remote_path)
        self.uploaded_documents[remote_path] = local_path.read_bytes()
        return SimpleNamespace(
            created=CreateFileResult(
                fs_id=200 + len(self.complete_upload_paths),
                path=remote_path,
                md5="d" * 32,
                server_filename=remote_path.rsplit("/", 1)[-1],
            )
        )

    def delete_files(self, remote_paths, *, async_mode: int = 0):
        assert async_mode == 0
        for path in remote_paths:
            self.deleted_paths.append(path)
            self.uploaded_documents.pop(path, None)
        return FileManagerResult(errno=0, info=tuple())


class FailAfterPrecreateBaiduClient(FakeBaiduClient):
    def upload_part(self, **_kwargs):
        raise BaiduNetdiskError("network interrupted", error_code="http_request_failed")


class MustNotCallBaiduClient(FakeBaiduClient):
    def precreate(self, **_kwargs):
        raise AssertionError("completed upload must not call precreate again")

    def locate_upload_server(self, **_kwargs):
        raise AssertionError("completed upload must not call locateupload again")

    def upload_part(self, **_kwargs):
        raise AssertionError("completed upload must not upload parts again")

    def create_file(self, **_kwargs):
        raise AssertionError("completed upload must not create file again")

    def upload_file_complete(self, **_kwargs):
        raise AssertionError("completed upload must not upload metadata again")


def test_metadata_documents_are_stable_and_exclude_sensitive_fields() -> None:
    created_at = datetime(2026, 6, 6, tzinfo=timezone.utc)
    archive_meta = build_archive_meta_document(
        ArchiveMetaInput(
            archive_id="archive-1",
            archive_seq=1,
            archive_sha256="a" * 64,
            archive_md5="b" * 32,
            archive_size=10,
            archive_type="payload",
            job_id="job-1",
            device_id="device-1",
            manifest_id="manifest-1",
            created_at=created_at,
        )
    )
    job_index = build_job_index_document(
        job_id="job-1",
        device_id="device-1",
        job_created_at=created_at,
        root_dir="/apps/auto_backup_bdnetdesk/backups",
        archives=(
            JobIndexArchive(
                archive_id="archive-1",
                archive_seq=1,
                archive_sha256="a" * 64,
                archive_size=10,
                archive_type="payload",
                remote_archive_path="/apps/auto_backup_bdnetdesk/backups/2026/06/06/device-1/job-1/archives/000001-" + ("a" * 64) + ".7z",
                remote_meta_path="/apps/auto_backup_bdnetdesk/backups/2026/06/06/device-1/job-1/archives/000001-" + ("a" * 64) + ".meta.json",
                fs_id=123,
                meta_sha256=archive_meta.sha256,
            ),
        ),
    )

    combined = archive_meta.text + job_index.text

    assert archive_meta.sha256 == build_archive_meta_document(
        ArchiveMetaInput(
            archive_id="archive-1",
            archive_seq=1,
            archive_sha256="a" * 64,
            archive_md5="b" * 32,
            archive_size=10,
            archive_type="payload",
            job_id="job-1",
            device_id="device-1",
            manifest_id="manifest-1",
            created_at=created_at,
        )
    ).sha256
    forbidden = ("original" + "_path", "original" + "_name", "pass" + "word", "device" + "_token", "access" + "_token", "refresh" + "_token", "wrapping" + "_key")
    for forbidden in forbidden:
        assert forbidden not in combined.lower()


def test_resumable_upload_reuses_uploadid_and_uploads_only_missing_parts(tmp_path) -> None:
    archive = tmp_path / "archive.7z"
    archive.write_bytes((b"a" * DEFAULT_PART_SIZE) + b"tail")
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    try:
        BaiduResumableUploader(store=store, baidu=FailAfterPrecreateBaiduClient(), updated_by_device_id="device-1").upload(
            ResumableArchiveInput(
                local_path=archive,
                job_id="job-1",
                device_id="device-1",
                account_id="account-1",
                job_created_at=datetime(2026, 6, 6, tzinfo=timezone.utc),
            )
        )
    except BaiduNetdiskError:
        pass

    baidu = FakeBaiduClient()
    second = BaiduResumableUploader(store=store, baidu=baidu, updated_by_device_id="device-1").upload(
        ResumableArchiveInput(
            local_path=archive,
            job_id="job-1",
            device_id="device-1",
            account_id="account-1",
            job_created_at=datetime(2026, 6, 6, tzinfo=timezone.utc),
        )
    )

    assert second.reused_uploadid is True
    assert baidu.precreate_uploadids[-1] == "fresh-uploadid"
    assert second.uploaded_partseqs == (1,)
    assert baidu.uploaded_partseqs[-1] == 1
    assert any(path.endswith(".meta.json") for path in baidu.complete_upload_paths)
    assert any(path.endswith("/job.index.json") for path in baidu.complete_upload_paths)

    with store.connect() as conn:
        session = conn.execute("SELECT * FROM upload_sessions WHERE upload_session_id = ?", (second.upload_session_id,)).fetchone()
        part_statuses = [row["status"] for row in conn.execute("SELECT status FROM upload_parts WHERE upload_session_id = ? ORDER BY partseq", (second.upload_session_id,)).fetchall()]
        outbox_count = conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0]

    assert session["upload_status"] == "remote_created"
    assert session["meta_status"] == "uploaded"
    assert session["job_index_status"] == "uploaded"
    assert part_statuses == ["confirmed", "confirmed"]
    assert outbox_count > 0


def test_resumable_upload_falls_back_when_uploadid_expires(tmp_path) -> None:
    archive = tmp_path / "archive.7z"
    archive.write_bytes((b"a" * DEFAULT_PART_SIZE) + b"tail")
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    try:
        BaiduResumableUploader(store=store, baidu=FailAfterPrecreateBaiduClient(), updated_by_device_id="device-1").upload(
            ResumableArchiveInput(
                local_path=archive,
                job_id="job-1",
                device_id="device-1",
                account_id="account-1",
                job_created_at=datetime(2026, 6, 6, tzinfo=timezone.utc),
            )
        )
    except BaiduNetdiskError:
        pass

    second_baidu = FakeBaiduClient(expire_uploadid=True)
    result = BaiduResumableUploader(store=store, baidu=second_baidu, updated_by_device_id="device-1").upload(
        ResumableArchiveInput(
            local_path=archive,
            job_id="job-1",
            device_id="device-1",
            account_id="account-1",
            job_created_at=datetime(2026, 6, 6, tzinfo=timezone.utc),
        )
    )

    assert second_baidu.precreate_uploadids[:2] == ["fresh-uploadid", ""]
    assert result.reused_uploadid is False
    assert result.uploadid == "fresh-uploadid"


def test_resumable_upload_empty_block_list_uploads_no_parts(tmp_path) -> None:
    archive = tmp_path / "archive.7z"
    archive.write_bytes(b"already uploaded")
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    baidu = FakeBaiduClient(block_list=tuple())

    result = BaiduResumableUploader(store=store, baidu=baidu, updated_by_device_id="device-1").upload(
        ResumableArchiveInput(
            local_path=archive,
            job_id="job-1",
            device_id="device-1",
            account_id="account-1",
            job_created_at=datetime(2026, 6, 6, tzinfo=timezone.utc),
        )
    )

    assert result.uploaded_partseqs == tuple()
    assert baidu.uploaded_partseqs == []

    with store.connect() as conn:
        part = conn.execute(
            "SELECT status, attempt_count FROM upload_parts WHERE upload_session_id = ?",
            (result.upload_session_id,),
        ).fetchone()

    assert part["status"] == "confirmed"
    assert part["attempt_count"] == 0


def test_resumable_upload_records_baidu_md5_for_metadata_objects(tmp_path) -> None:
    archive = tmp_path / "archive.7z"
    archive.write_bytes(b"payload")
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()

    result = BaiduResumableUploader(store=store, baidu=FakeBaiduClient(), updated_by_device_id="device-1").upload(
        ResumableArchiveInput(
            local_path=archive,
            job_id="job-1",
            device_id="device-1",
            account_id="account-1",
            job_created_at=datetime(2026, 6, 6, tzinfo=timezone.utc),
        )
    )

    with store.connect() as conn:
        rows = {
            row["object_type"]: row
            for row in conn.execute(
                "SELECT object_type, md5, sha256 FROM remote_objects WHERE object_type IN ('archive_meta', 'job_index')",
            ).fetchall()
        }

    assert rows["archive_meta"]["md5"] == result.meta_created.md5
    assert rows["job_index"]["md5"] == result.job_index_created.md5
    assert rows["archive_meta"]["sha256"] == result.archive_meta.sha256
    assert rows["job_index"]["sha256"] == result.job_index.sha256
    assert rows["archive_meta"]["md5"] != hashlib.md5(result.archive_meta.bytes).hexdigest()
    assert rows["job_index"]["md5"] != hashlib.md5(result.job_index.bytes).hexdigest()


def test_resumable_upload_job_index_summarizes_all_archives(tmp_path) -> None:
    first_archive = tmp_path / "first.7z"
    second_archive = tmp_path / "second.7z"
    first_archive.write_bytes(b"first payload")
    second_archive.write_bytes(b"second payload")
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    baidu = FakeBaiduClient()
    uploader = BaiduResumableUploader(store=store, baidu=baidu, updated_by_device_id="device-1")

    first = uploader.upload(
        ResumableArchiveInput(
            local_path=first_archive,
            job_id="job-1",
            device_id="device-1",
            account_id="account-1",
            archive_seq=1,
            job_created_at=datetime(2026, 6, 6, tzinfo=timezone.utc),
        )
    )
    second = uploader.upload(
        ResumableArchiveInput(
            local_path=second_archive,
            job_id="job-1",
            device_id="device-1",
            account_id="account-1",
            archive_seq=2,
            job_created_at=datetime(2026, 6, 6, tzinfo=timezone.utc),
        )
    )

    assert baidu.deleted_paths == [first.remote_job_index_path]
    index_data = json.loads(baidu.uploaded_documents[second.remote_job_index_path].decode("utf-8"))
    assert [item["archive_id"] for item in index_data["archives"]] == [first.archive_id, second.archive_id]
    assert [item["archive_seq"] for item in index_data["archives"]] == [1, 2]
    assert second.job_index.data == index_data


def test_resumable_upload_completed_session_returns_local_state_without_reupload(tmp_path) -> None:
    archive = tmp_path / "archive.7z"
    archive.write_bytes((b"a" * DEFAULT_PART_SIZE) + b"tail")
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    first = BaiduResumableUploader(store=store, baidu=FakeBaiduClient(), updated_by_device_id="device-1").upload(
        ResumableArchiveInput(
            local_path=archive,
            job_id="job-1",
            device_id="device-1",
            account_id="account-1",
            job_created_at=datetime(2026, 6, 6, tzinfo=timezone.utc),
        )
    )

    second = BaiduResumableUploader(store=store, baidu=MustNotCallBaiduClient(), updated_by_device_id="device-1").upload(
        ResumableArchiveInput(
            local_path=archive,
            job_id="job-1",
            device_id="device-1",
            account_id="account-1",
            job_created_at=datetime(2026, 6, 6, tzinfo=timezone.utc),
        )
    )

    assert second.upload_session_id == first.upload_session_id
    assert second.remote_archive_path == first.remote_archive_path
    assert second.uploaded_partseqs == tuple()
    assert second.created.fs_id == first.created.fs_id
