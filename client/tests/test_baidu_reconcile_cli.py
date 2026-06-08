from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from auto_backup_client.baidu import reconcile_cli
from auto_backup_client.baidu.reconcile_cli import main
from auto_backup_client.baidu.upload import BaiduFileItem, BaiduFileListResult
from auto_backup_client.device_credentials import DeviceCredentialStore, DeviceCredentials
from auto_backup_client.sqlite_store import SQLiteClientStore, build_version_fields


NOW = "2026-06-08T00:00:00Z"
JOB_DIR = "/apps/auto_backup_bdnetdesk/backups/2026/06/08/device-secret/job-secret"
REMOTE_PATH = f"{JOB_DIR}/archives/000001-{'a' * 64}.7z"


@dataclass(frozen=True)
class FakeEncrypted:
    account_id: str = "account-secret"
    token_version: int = 8


@dataclass(frozen=True)
class FakeToken:
    access_token: str = "secret-access-token"


class FakeCloudClient:
    def __init__(self, base_url: str, device_token: str, *, timeout: float = 20.0) -> None:
        del base_url, timeout
        self.device_token = device_token

    def __enter__(self):
        return self

    def __exit__(self, *_exc: object) -> None:
        pass


class FakeBaiduNetdiskClient:
    instances: list["FakeBaiduNetdiskClient"] = []

    def __init__(self, access_token: str, *, timeout: float = 60.0) -> None:
        del timeout
        self.access_token = access_token
        FakeBaiduNetdiskClient.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_exc: object) -> None:
        pass

    def list_all(self, *, remote_path: str, start: int = 0, limit: int = 1000, recursion: bool = True, web: bool = False):
        del limit, recursion, web
        assert remote_path == JOB_DIR
        assert start == 0
        return BaiduFileListResult(
            errno=0,
            items=(BaiduFileItem(fs_id=202, path=REMOTE_PATH, server_filename="archive.7z", isdir=False, size=12, md5="c" * 32),),
        )


def test_reconcile_cli_outputs_redacted_report(tmp_path, capsys, monkeypatch) -> None:
    db_path = tmp_path / "sensitive-state.sqlite3"
    store = SQLiteClientStore(db_path)
    store.migrate()
    _insert_remote_object(store, remote_path=REMOTE_PATH)
    _install_common_fakes(tmp_path, monkeypatch)

    assert (
        main(
            [
                "--sqlite-path",
                str(db_path),
                "--password-env",
                "BAIDU_AUTH_PASSWORD",
                "remote-objects",
                "--job-id",
                "job-secret",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out

    assert "status_remote_size_mismatch: 1" in output
    assert "finding_1_remote_path_sha256:" in output
    assert "finding_1_size: 10->12" in output
    assert "远端对象校对完成: read-only report" in output
    assert "secret-device-token" not in output
    assert "secret-access-token" not in output
    assert "runtime-password-secret" not in output
    assert str(db_path) not in output
    assert "sensitive-state.sqlite3" not in output
    assert REMOTE_PATH not in output
    assert "job-secret/archives" not in output


def test_repair_cli_defaults_to_dry_run_without_writing(tmp_path, capsys, monkeypatch) -> None:
    db_path = tmp_path / "sensitive-state.sqlite3"
    store = SQLiteClientStore(db_path)
    store.migrate()
    _insert_remote_object(store, remote_path=REMOTE_PATH)
    _install_common_fakes(tmp_path, monkeypatch)

    assert (
        main(
            [
                "--sqlite-path",
                str(db_path),
                "--password-env",
                "BAIDU_AUTH_PASSWORD",
                "repair-remote-objects",
                "--job-id",
                "job-secret",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    with store.connect() as conn:
        remote = conn.execute("SELECT size_bytes, md5, fs_id, data_version FROM remote_objects").fetchone()
        outbox_count = conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0]

    assert "dry_run: true" in output
    assert "candidate_1_action: accept_baidu_metadata" in output
    assert "selected_count: 1" in output
    assert "applied_count: 0" in output
    assert remote["size_bytes"] == 10
    assert remote["md5"] == "b" * 32
    assert remote["fs_id"] == 101
    assert remote["data_version"] == 1
    assert outbox_count == 1
    assert "secret-device-token" not in output
    assert "secret-access-token" not in output
    assert "runtime-password-secret" not in output
    assert str(db_path) not in output
    assert REMOTE_PATH not in output


def test_repair_cli_requires_confirmation_before_apply(tmp_path, capsys, monkeypatch) -> None:
    db_path = tmp_path / "sensitive-state.sqlite3"
    store = SQLiteClientStore(db_path)
    store.migrate()
    _insert_remote_object(store, remote_path=REMOTE_PATH)
    _install_common_fakes(tmp_path, monkeypatch)

    assert (
        main(
            [
                "--sqlite-path",
                str(db_path),
                "--password-env",
                "BAIDU_AUTH_PASSWORD",
                "repair-remote-objects",
                "--job-id",
                "job-secret",
                "--apply",
            ]
        )
        == 1
    )
    output = capsys.readouterr().out

    assert "repair confirmation is required" in output
    assert str(db_path) not in output
    assert "runtime-password-secret" not in output


def test_repair_cli_confirmed_apply_writes_version_and_outbox(tmp_path, capsys, monkeypatch) -> None:
    db_path = tmp_path / "sensitive-state.sqlite3"
    store = SQLiteClientStore(db_path)
    store.migrate()
    _insert_remote_object(store, remote_path=REMOTE_PATH)
    _install_common_fakes(tmp_path, monkeypatch)

    assert (
        main(
            [
                "--sqlite-path",
                str(db_path),
                "--password-env",
                "BAIDU_AUTH_PASSWORD",
                "repair-remote-objects",
                "--job-id",
                "job-secret",
                "--apply",
                "--confirm",
                "APPLY_REMOTE_REPAIR",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    with store.connect() as conn:
        remote = conn.execute("SELECT size_bytes, md5, fs_id, data_version, updated_by_device_id, revision_id FROM remote_objects").fetchone()
        outbox = conn.execute("SELECT revision_id, status, payload_json FROM sync_outbox ORDER BY created_at, rowid").fetchall()

    assert "dry_run: false" in output
    assert "applied_count: 1" in output
    assert "applied_1_remote_object_id_sha256:" in output
    assert remote["size_bytes"] == 12
    assert remote["md5"] == "c" * 32
    assert remote["fs_id"] == 202
    assert remote["data_version"] == 2
    assert remote["updated_by_device_id"] == "device-secret"
    assert len(outbox) == 2
    assert outbox[-1]["revision_id"] == remote["revision_id"]
    assert outbox[-1]["status"] == "pending"
    assert '"size_bytes":12' in outbox[-1]["payload_json"]
    assert str(db_path) not in output
    assert REMOTE_PATH not in output


def test_reconcile_cli_scope_errors_are_safe(tmp_path, capsys, monkeypatch) -> None:
    db_path = tmp_path / "sensitive-state.sqlite3"
    _install_common_fakes(tmp_path, monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "--sqlite-path",
                str(db_path),
                "--password-env",
                "BAIDU_AUTH_PASSWORD",
                "remote-objects",
                "--job-id",
                "job-secret",
                "--remote-dir",
                JOB_DIR,
            ]
        )
    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    output = captured.err + captured.out
    assert str(db_path) not in output
    assert JOB_DIR not in output


def test_reconcile_cli_validation_failure_does_not_print_secrets(tmp_path, capsys, monkeypatch) -> None:
    db_path = tmp_path / "sensitive-state.sqlite3"
    monkeypatch.setenv("BAIDU_AUTH_PASSWORD", "runtime-password-secret")

    assert (
        main(
            [
                "--sqlite-path",
                str(db_path),
                "--password-env",
                "BAIDU_AUTH_PASSWORD",
                "remote-objects",
                "--job-id",
                "job-secret",
                "--page-limit",
                "0",
            ]
        )
        == 1
    )
    output = capsys.readouterr().out

    assert "page_limit must be >= 1" in output
    assert "runtime-password-secret" not in output
    assert str(db_path) not in output
    assert "sensitive-state.sqlite3" not in output


def _install_common_fakes(tmp_path, monkeypatch) -> None:
    FakeBaiduNetdiskClient.instances.clear()
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
    monkeypatch.setenv("BAIDU_AUTH_PASSWORD", "runtime-password-secret")
    monkeypatch.delenv("CLOUD_API_DEVICE_TOKEN", raising=False)
    monkeypatch.setattr(reconcile_cli, "BaiduCloudClient", FakeCloudClient)
    monkeypatch.setattr(reconcile_cli, "BaiduNetdiskClient", FakeBaiduNetdiskClient)
    monkeypatch.setattr(
        reconcile_cli,
        "_decrypt_selected_token",
        lambda cloud, account_id, password: SimpleNamespace(encrypted=FakeEncrypted(), token=FakeToken()),
    )


def _insert_remote_object(store: SQLiteClientStore, *, remote_path: str) -> None:
    with store.transaction() as conn:
        payload = build_version_fields(
            entity_payload={
                "remote_object_id": "remote-archive",
                "entity_id": "remote_object_archive",
                "object_type": "archive",
                "job_id": "job-secret",
                "device_id": "device-secret",
                "archive_id": "archive-1",
                "archive_sha256": "a" * 64,
                "remote_path": remote_path,
                "size_bytes": 10,
                "md5": "b" * 32,
                "sha256": "a" * 64,
                "fs_id": 101,
                "status": "remote_created",
                "created_at": NOW,
            },
            updated_by_device_id="device-secret",
            now=NOW,
            revision_id="rev-1",
        )
        store.put_remote_object(conn, payload)
