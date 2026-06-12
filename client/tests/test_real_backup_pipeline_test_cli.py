from __future__ import annotations

from types import SimpleNamespace

from auto_backup_client import real_backup_pipeline_test_cli as cli
from auto_backup_client.baidu.cloud_api import CloudAPIError
from auto_backup_client.baidu.models import SyncRevisionResult
from auto_backup_client.baidu.upload import BaiduQuota, CreateFileResult, FileManagerResult
from auto_backup_client.device_credentials import DeviceCredentialStore, DeviceCredentials
from auto_backup_client.sqlite_store import SQLiteClientStore


class FakeCloudClient:
    events_by_entity: dict[str, object] = {}

    def __init__(self, base_url: str, device_token: str, *, timeout: float = 30.0, device_id: str = "") -> None:
        del base_url, timeout
        self.device_token = device_token
        self.device_id = device_id
        assert device_id == "device-secret"

    def __enter__(self):
        return self

    def __exit__(self, *_exc: object) -> None:
        pass

    def close(self) -> None:
        pass

    def sync_revisions(self, events):
        FakeCloudClient.events_by_entity.update({event.entity_id: event for event in events})
        return [
            SyncRevisionResult(
                event_id=event.event_id,
                entity_id=event.entity_id,
                revision_id=event.revision_id,
                status="synced",
                cloud_data_version=event.data_version,
                cloud_revision_id=event.revision_id,
            )
            for event in events
        ]

    def get_entity_summary(self, entity_id: str):
        event = FakeCloudClient.events_by_entity[entity_id]
        return SimpleNamespace(
            revision_id=event.revision_id,
            data_version=event.data_version,
            canonical_record_sha256=event.canonical_record_sha256,
            recent_revisions=tuple(),
        )

    def get_content(self, content_id: str):
        raise CloudAPIError(404, "not_found", f"content not found: {content_id}")


class FakeBaiduClient:
    instances: list["FakeBaiduClient"] = []

    def __init__(self, access_token: str, *, timeout: float = 120.0) -> None:
        del timeout
        self.access_token = access_token
        self.created: dict[str, tuple[int, int, str]] = {}
        self.uploaded_partseqs: list[int] = []
        self.deleted_paths: tuple[str, ...] = tuple()
        self.deleted_path_history: list[str] = []
        FakeBaiduClient.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_exc: object) -> None:
        pass

    def close(self) -> None:
        pass

    def get_quota(self, *, checkfree: bool = True, checkexpire: bool = True):
        del checkfree, checkexpire
        return BaiduQuota(total=2_000_000_000, used=1)

    def precreate(self, **kwargs):
        if kwargs["remote_path"] in self.created:
            from auto_backup_client.baidu.upload import BaiduNetdiskError

            raise BaiduNetdiskError("same path exists", error_code="-8")
        return SimpleNamespace(path=kwargs["remote_path"], uploadid="fake-uploadid", return_type=1, block_list=tuple(range(len(kwargs["block_md5s"]))))

    def locate_upload_server(self, **_kwargs):
        return SimpleNamespace(upload_server="https://upload.example.test", servers=("https://upload.example.test",))

    def upload_part(self, **kwargs):
        partseq = kwargs["partseq"]
        self.uploaded_partseqs.append(partseq)
        part = kwargs["plan"].part_by_seq(partseq)
        return SimpleNamespace(partseq=partseq, md5=part.md5)

    def create_file(self, **kwargs):
        remote_path = kwargs["remote_path"]
        result = CreateFileResult(
            fs_id=100 + len(self.created),
            path=remote_path,
            md5="a" * 32,
            server_filename=remote_path.rsplit("/", 1)[-1],
        )
        self.created[remote_path] = (result.fs_id, int(kwargs["size"]), result.md5)
        return result

    def upload_file_complete(self, *, local_path, remote_path: str, part_size: int, rtype: int):
        del part_size, rtype
        if remote_path in self.created:
            from auto_backup_client.baidu.upload import BaiduNetdiskError

            raise BaiduNetdiskError("same path exists", error_code="-8")
        result = CreateFileResult(
            fs_id=100 + len(self.created),
            path=remote_path,
            md5="b" * 32,
            server_filename=remote_path.rsplit("/", 1)[-1],
        )
        self.created[remote_path] = (result.fs_id, local_path.stat().st_size, result.md5)
        return SimpleNamespace(created=result)

    def list_all(self, *, remote_path: str, start: int = 0, limit: int = 1000, recursion: bool = True, web: bool = False):
        del remote_path, start, limit, recursion, web
        from auto_backup_client.baidu.upload import BaiduFileItem, BaiduFileListResult

        return BaiduFileListResult(
            errno=0,
            items=tuple(
                BaiduFileItem(
                    fs_id=fs_id,
                    path=path,
                    server_filename=path.rsplit("/", 1)[-1],
                    isdir=False,
                    size=size,
                    md5=md5,
                )
                for path, (fs_id, size, md5) in sorted(self.created.items())
            ),
        )

    def delete_files(self, remote_paths, *, async_mode: int = 0):
        assert async_mode == 0
        self.deleted_paths = tuple(remote_paths)
        self.deleted_path_history.extend(remote_paths)
        for path in remote_paths:
            self.created.pop(path, None)
        return FileManagerResult(errno=0, info=tuple())


def test_real_pipeline_cli_runs_full_flow_and_redacts_sensitive_values(tmp_path, monkeypatch, capsys) -> None:
    FakeBaiduClient.instances.clear()
    FakeCloudClient.events_by_entity = {}
    _install_credentials(tmp_path, monkeypatch)
    monkeypatch.setenv("BAIDU_AUTH_PASSWORD", "runtime-password-secret")
    monkeypatch.setattr(cli, "BaiduCloudClient", FakeCloudClient)
    monkeypatch.setattr(cli, "BaiduNetdiskClient", FakeBaiduClient)
    monkeypatch.setattr(
        cli,
        "_decrypt_selected_token",
        lambda cloud, account_id, password: SimpleNamespace(
            encrypted=SimpleNamespace(account_id="account-secret", token_version=3),
            token=SimpleNamespace(access_token="secret-access-token"),
        ),
    )
    sqlite_path = tmp_path / "sensitive-state.sqlite3"
    cache_root = tmp_path / "cache-secret"
    work_dir = tmp_path / "sources-secret"

    exit_code = cli.main(
        [
            "--sqlite-path",
            str(sqlite_path),
            "--cache-root",
            str(cache_root),
            "--work-dir",
            str(work_dir),
            "--password-env",
            "BAIDU_AUTH_PASSWORD",
            "--small-bytes",
            "32",
            "--multipart-bytes",
            str(4 * 1024 * 1024 + 33),
        ]
    )

    output = capsys.readouterr().out
    baidu = FakeBaiduClient.instances[-1]
    store = SQLiteClientStore(sqlite_path)
    jobs = store.list_backup_jobs()

    assert exit_code == 0, output
    assert len(jobs) == 1
    assert jobs[0]["status"] == "completed"
    assert "completed: true" in output
    assert "archive_count: 2" in output
    assert "upload_count: 2" in output
    assert "uploaded_part_total_count: 3" in output
    assert "reconcile_consistent: 5" in output
    assert "completed_job_cloud_summary_verified: true" in output
    assert "conflict_probe_detected: true" in output
    assert "cleanup_object_count: 5" in output
    assert "cleanup_delete_errno: 0" in output
    assert len(baidu.deleted_paths) == 5
    assert "secret-device-token" not in output
    assert "secret-access-token" not in output
    assert "runtime-password-secret" not in output
    assert "account-secret" not in output
    assert str(sqlite_path) not in output
    assert str(cache_root) not in output
    assert str(work_dir) not in output
    assert "/apps/auto_backup_bdnetdesk/backups" not in output


def test_real_pipeline_cli_reports_invalid_multipart_without_path_leak(tmp_path, monkeypatch, capsys) -> None:
    _install_credentials(tmp_path, monkeypatch)
    monkeypatch.setenv("BAIDU_AUTH_PASSWORD", "runtime-password-secret")

    exit_code = cli.main(
        [
            "--sqlite-path",
            str(tmp_path / "sensitive-state.sqlite3"),
            "--password-env",
            "BAIDU_AUTH_PASSWORD",
            "--multipart-bytes",
            "1",
        ]
    )

    output = capsys.readouterr().out

    assert exit_code == 1
    assert "multipart_bytes must exceed one upload part" in output
    assert str(tmp_path) not in output
    assert "runtime-password-secret" not in output


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
