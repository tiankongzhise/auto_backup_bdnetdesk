from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace

from auto_backup_client.baidu import integration_cli
from auto_backup_client.baidu.integration_cli import main
from auto_backup_client.baidu.resumable_upload import ResumableUploadResult
from auto_backup_client.baidu.upload import BaiduQuota, CreateFileResult, FileManagerResult
from auto_backup_client.device_credentials import DeviceCredentialStore, DeviceCredentials
from auto_backup_client.sqlite_store import SQLiteClientStore, build_version_fields
from auto_backup_client.sync_worker import SyncWorkerResult


NOW = "2026-06-07T00:00:00Z"
REMOTE_ARCHIVE_PATH = "/apps/auto_backup_bdnetdesk/backups/2026/06/07/device-secret/job-secret/archives/000001-aaaaaaaa.7z"
REMOTE_META_PATH = "/apps/auto_backup_bdnetdesk/backups/2026/06/07/device-secret/job-secret/archives/000001-aaaaaaaa.meta.json"
REMOTE_JOB_INDEX_PATH = "/apps/auto_backup_bdnetdesk/backups/2026/06/07/device-secret/job-secret/job.index.json"


@dataclass(frozen=True)
class FakeEncrypted:
    account_id: str = "account-secret"
    token_version: int = 7


@dataclass(frozen=True)
class FakeToken:
    access_token: str = "secret-access-token"


class FakeCloudClient:
    summary_events_by_entity: dict[str, object] = {}

    def __init__(self, base_url: str, device_token: str, *, timeout: float = 20.0) -> None:
        del base_url, timeout
        self.device_token = device_token
        self.summary_entity_ids: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc: object) -> None:
        pass

    def get_entity_summary(self, entity_id: str):
        self.summary_entity_ids.append(entity_id)
        event = self.summary_events_by_entity[entity_id]
        return SimpleNamespace(
            revision_id=event.revision_id,
            data_version=event.data_version,
            canonical_record_sha256=event.canonical_record_sha256,
            recent_revisions=tuple(),
        )


class FakeBaiduNetdiskClient:
    instances: list["FakeBaiduNetdiskClient"] = []

    def __init__(self, access_token: str, *, timeout: float = 60.0) -> None:
        del timeout
        self.access_token = access_token
        self.deleted_paths: tuple[str, ...] = tuple()
        FakeBaiduNetdiskClient.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_exc: object) -> None:
        pass

    def get_quota(self):
        return BaiduQuota(total=10_000_000, used=1)

    def delete_files(self, remote_paths, *, async_mode: int = 0):
        assert async_mode == 0
        self.deleted_paths = tuple(remote_paths)
        return FileManagerResult(errno=0, info=tuple())


class FakeUploader:
    calls: list[object] = []

    def __init__(self, *, store, baidu, updated_by_device_id: str) -> None:
        self.store = store
        self.baidu = baidu
        self.updated_by_device_id = updated_by_device_id

    def upload(self, value):
        FakeUploader.calls.append(value)
        result = _insert_uploaded_state(self.store, value.job_id, value.device_id, value.account_id, str(value.local_path))
        return result


class FakeSyncWorker:
    results_by_status = {
        "synced": (1, 0, 0, 0),
    }

    def __init__(self, *, store, cloud, batch_size: int = 100) -> None:
        del cloud
        self.store = store
        self.batch_size = batch_size

    def run_once(self):
        selected = self.store.list_outbox_events_for_sync(limit=self.batch_size, now=NOW)
        if not selected:
            return SyncWorkerResult(selected=0, sent=0, synced=0, conflicts=0, rejected=0, retryable=0)
        FakeCloudClient.summary_events_by_entity = {event.entity_id: event for event in selected}
        status = getattr(FakeSyncWorker, "status", "synced")
        synced, conflicts, rejected, retryable = {
            "synced": (len(selected), 0, 0, 0),
            "conflict": (0, len(selected), 0, 0),
            "rejected": (0, 0, len(selected), 0),
            "retryable": (0, 0, 0, len(selected)),
        }[status]
        revision_results = tuple(
            SimpleNamespace(
                event_id=event.event_id,
                entity_id=event.entity_id,
                revision_id=event.revision_id,
                status="synced" if status == "synced" else status,
            )
            for event in selected
        )
        return SyncWorkerResult(
            selected=len(selected),
            sent=len(selected),
            synced=synced,
            conflicts=conflicts,
            rejected=rejected,
            retryable=retryable,
            revision_results=revision_results,
        )


def test_run_resumable_cli_uploads_syncs_verifies_and_cleans_up(tmp_path, capsys, monkeypatch) -> None:
    db_path = tmp_path / "sensitive-state.sqlite3"
    _install_common_fakes(tmp_path, db_path, monkeypatch)
    monkeypatch.setattr(FakeSyncWorker, "status", "synced", raising=False)

    assert (
        main(
            [
                "--sqlite-path",
                str(db_path),
                "run-resumable",
                "--password-env",
                "BAIDU_AUTH_PASSWORD",
                "--job-id",
                "job-secret",
                "--device-id",
                "device-secret",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    baidu = FakeBaiduNetdiskClient.instances[-1]

    assert "sync_selected: 4" in output
    assert "sync_synced: 4" in output
    assert "cloud_summary_verified: 4" in output
    assert "cleanup_object_count: 3" in output
    assert "cleanup_delete_errno: 0" in output
    assert len(baidu.deleted_paths) == 3
    assert REMOTE_ARCHIVE_PATH in baidu.deleted_paths
    assert "secret-device-token" not in output
    assert "secret-access-token" not in output
    assert "runtime-password-secret" not in output
    assert str(db_path) not in output
    assert "sensitive-state.sqlite3" not in output
    assert str(tmp_path) not in output
    assert REMOTE_ARCHIVE_PATH not in output
    assert "job-secret/archives" not in output


def test_run_resumable_keep_remote_prints_hashes_without_deleting(tmp_path, capsys, monkeypatch) -> None:
    db_path = tmp_path / "backup_state.sqlite3"
    _install_common_fakes(tmp_path, db_path, monkeypatch)

    assert (
        main(
            [
                "--sqlite-path",
                str(db_path),
                "--password-env",
                "BAIDU_AUTH_PASSWORD",
                "run-resumable",
                "--job-id",
                "job-secret",
                "--device-id",
                "device-secret",
                "--keep-remote",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    baidu = FakeBaiduNetdiskClient.instances[-1]

    assert "cleanup_kept_remote: true" in output
    assert "cleanup_path_1_sha256:" in output
    assert baidu.deleted_paths == tuple()
    assert REMOTE_META_PATH not in output


def test_cleanup_resumable_deletes_remote_objects_from_sqlite(tmp_path, capsys, monkeypatch) -> None:
    db_path = tmp_path / "backup_state.sqlite3"
    store = SQLiteClientStore(db_path)
    store.migrate()
    _insert_uploaded_state(store, "job-secret", "device-secret", "account-secret", "C:/sensitive/archive.7z")
    _install_credentials(tmp_path, monkeypatch)
    monkeypatch.setenv("BAIDU_AUTH_PASSWORD", "runtime-password-secret")
    monkeypatch.setattr(integration_cli, "BaiduCloudClient", FakeCloudClient)
    monkeypatch.setattr(integration_cli, "BaiduNetdiskClient", FakeBaiduNetdiskClient)
    monkeypatch.setattr(
        integration_cli,
        "_decrypt_selected_token",
        lambda cloud, account_id, password: SimpleNamespace(encrypted=FakeEncrypted(), token=FakeToken()),
    )

    assert (
        main(
            [
                "--sqlite-path",
                str(db_path),
                "--password-env",
                "BAIDU_AUTH_PASSWORD",
                "cleanup-resumable",
                "--upload-session-id",
                "session-1",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    baidu = FakeBaiduNetdiskClient.instances[-1]

    assert "cleanup_object_count: 3" in output
    assert "cleanup_delete_errno: 0" in output
    assert len(baidu.deleted_paths) == 3
    assert REMOTE_JOB_INDEX_PATH in baidu.deleted_paths
    assert "C:/sensitive/archive.7z" not in output
    assert REMOTE_JOB_INDEX_PATH not in output


def test_run_resumable_prints_conflict_rejected_and_retryable_counts(tmp_path, capsys, monkeypatch) -> None:
    statuses = ("conflict", "rejected", "retryable")
    expected = {
        "conflict": "sync_conflicts: 4",
        "rejected": "sync_rejected: 4",
        "retryable": "sync_retryable: 4",
    }
    for status in statuses:
        db_path = tmp_path / status / "backup_state.sqlite3"
        _install_common_fakes(tmp_path / status, db_path, monkeypatch)
        monkeypatch.setattr(FakeSyncWorker, "status", status, raising=False)

        assert (
            main(
                [
                    "--sqlite-path",
                    str(db_path),
                    "--password-env",
                    "BAIDU_AUTH_PASSWORD",
                    "run-resumable",
                    "--job-id",
                    "job-secret",
                    "--device-id",
                    "device-secret",
                    "--no-verify-cloud-summary",
                    "--keep-remote",
                ]
            )
            == 0
        )
        output = capsys.readouterr().out
        assert expected[status] in output
        assert REMOTE_ARCHIVE_PATH not in output


def test_cli_failure_does_not_print_sensitive_paths(tmp_path, capsys, monkeypatch) -> None:
    db_path = tmp_path / "sensitive-state.sqlite3"
    monkeypatch.setenv("BAIDU_AUTH_PASSWORD", "runtime-password-secret")

    assert (
        main(
            [
                "--sqlite-path",
                str(db_path),
                "--password-env",
                "BAIDU_AUTH_PASSWORD",
                "run-resumable",
                "--archive-size-bytes",
                "0",
            ]
        )
        == 1
    )

    output = capsys.readouterr().out
    assert "archive_size_bytes must be >= 1" in output
    assert str(db_path) not in output
    assert "sensitive-state.sqlite3" not in output
    assert "runtime-password-secret" not in output


def _install_common_fakes(tmp_path, db_path, monkeypatch) -> None:
    FakeBaiduNetdiskClient.instances.clear()
    FakeUploader.calls.clear()
    FakeCloudClient.summary_events_by_entity = {}
    _install_credentials(tmp_path, monkeypatch)
    monkeypatch.setenv("BAIDU_AUTH_PASSWORD", "runtime-password-secret")
    monkeypatch.setattr(integration_cli, "BaiduCloudClient", FakeCloudClient)
    monkeypatch.setattr(integration_cli, "BaiduNetdiskClient", FakeBaiduNetdiskClient)
    monkeypatch.setattr(integration_cli, "BaiduResumableUploader", FakeUploader)
    monkeypatch.setattr(integration_cli, "SyncOutboxWorker", FakeSyncWorker)
    monkeypatch.setattr(
        integration_cli,
        "_decrypt_selected_token",
        lambda cloud, account_id, password: SimpleNamespace(encrypted=FakeEncrypted(), token=FakeToken()),
    )
    monkeypatch.setattr(integration_cli, "_write_temp_archive", lambda path, size_bytes: path.write_bytes(b"test archive"))
    monkeypatch.setattr(integration_cli, "DEFAULT_ARCHIVE_SIZE_BYTES", 32)
    db_path.parent.mkdir(parents=True, exist_ok=True)


def _install_credentials(tmp_path, monkeypatch) -> None:
    credential_store = DeviceCredentialStore(tmp_path / "device_credentials.json", allow_plaintext=True)
    credential_store.save(
        DeviceCredentials(
            cloud_api_base_url="https://backup.baichengedu.com",
            device_id="device-secret",
            device_token="secret-device-token",
        )
    )
    monkeypatch.setenv("AUTO_BACKUP_DEVICE_CREDENTIAL_STORE_PATH", str(credential_store.path))
    monkeypatch.setenv("AUTO_BACKUP_DEVICE_CREDENTIAL_STORE_ALLOW_PLAINTEXT", "true")
    monkeypatch.delenv("CLOUD_API_DEVICE_TOKEN", raising=False)


def _insert_uploaded_state(store: SQLiteClientStore, job_id: str, device_id: str, account_id: str, local_archive_path: str) -> ResumableUploadResult:
    with store.transaction() as conn:
        session = build_version_fields(
            entity_payload={
                "upload_session_id": "session-1",
                "entity_id": "upload_session_session-1",
                "job_id": job_id,
                "device_id": device_id,
                "account_id": account_id,
                "archive_id": "archive-1",
                "archive_seq": 1,
                "archive_sha256": "a" * 64,
                "archive_md5": "b" * 32,
                "archive_size": 12,
                "archive_type": "payload",
                "local_archive_path": local_archive_path,
                "remote_archive_path": REMOTE_ARCHIVE_PATH,
                "remote_meta_path": REMOTE_META_PATH,
                "remote_job_index_path": REMOTE_JOB_INDEX_PATH,
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
            updated_by_device_id=device_id,
            now=NOW,
            revision_id="rev-1",
        )
        store.put_upload_session(conn, session)
        for object_type, remote_path, fs_id in (
            ("archive", REMOTE_ARCHIVE_PATH, 101),
            ("archive_meta", REMOTE_META_PATH, 102),
            ("job_index", REMOTE_JOB_INDEX_PATH, 103),
        ):
            payload = build_version_fields(
                entity_payload={
                    "remote_object_id": f"remote-{object_type}",
                    "entity_id": f"remote_object_{object_type}",
                    "object_type": object_type,
                    "job_id": job_id,
                    "device_id": device_id,
                    "archive_id": "archive-1",
                    "archive_sha256": "a" * 64,
                    "remote_path": remote_path,
                    "size_bytes": 12,
                    "md5": "b" * 32,
                    "sha256": "a" * 64,
                    "fs_id": fs_id,
                    "status": "remote_created",
                    "created_at": NOW,
                },
                updated_by_device_id=device_id,
                now=NOW,
                revision_id="rev-1",
            )
            store.put_remote_object(conn, payload)
    created = CreateFileResult(fs_id=101, path=REMOTE_ARCHIVE_PATH, md5="b" * 32, server_filename="archive.7z")
    meta_created = CreateFileResult(fs_id=102, path=REMOTE_META_PATH, md5="c" * 32, server_filename="archive.meta.json")
    job_index_created = CreateFileResult(fs_id=103, path=REMOTE_JOB_INDEX_PATH, md5="d" * 32, server_filename="job.index.json")
    document = SimpleNamespace(text="{}", sha256="e" * 64, bytes=b"{}")
    return ResumableUploadResult(
        upload_session_id="session-1",
        archive_id="archive-1",
        archive_sha256="a" * 64,
        remote_archive_path=REMOTE_ARCHIVE_PATH,
        remote_meta_path=REMOTE_META_PATH,
        remote_job_index_path=REMOTE_JOB_INDEX_PATH,
        uploadid="secret-uploadid",
        reused_uploadid=False,
        uploaded_partseqs=(0,),
        created=created,
        meta_created=meta_created,
        job_index_created=job_index_created,
        archive_meta=document,
        job_index=document,
    )
