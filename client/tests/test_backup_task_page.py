from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from auto_backup_client.backup_jobs import BackupJobManager
from auto_backup_client.backup_pipeline import BackupPipeline, BackupPipelineOptions
from auto_backup_client.baidu.upload import BaiduFileItem, BaiduFileListResult
from auto_backup_client.dedupe_index import ContentDedupeIndexer
from auto_backup_client.scan_fingerprints import BackupScanner
from auto_backup_client.sqlite_store import SQLiteClientStore, build_version_fields
from auto_backup_client.ui import main_window
from auto_backup_client.ui.main_window import BackupTaskPage, RemoteReconcilePage, RestorePage, SourceMappingPage
from test_backup_pipeline import FakeBaiduForPipeline, FakeCloudForPipeline


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_backup_task_page_creates_persistent_job_without_status_path_leak(tmp_path, monkeypatch) -> None:
    _app()
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)
    source = tmp_path / "secret-source.txt"
    source.write_text("hello", encoding="utf-8")
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    page = BackupTaskPage(BackupJobManager(store, device_id="device-1"))
    status_messages: list[str] = []
    page.status_changed.connect(status_messages.append)

    page.add_sources([str(source)])
    page.job_name_input.setText("daily docs")
    page.create_job()

    assert page.jobs_table.rowCount() == 1
    assert page.jobs_table.item(0, 0).text() == "daily docs"
    assert page.jobs_table.item(0, 1).text() == "待开始"
    assert str(source) not in status_messages[-1]
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM backup_jobs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM backup_sources").fetchone()[0] == 1


def test_backup_task_page_transitions_selected_job(tmp_path, monkeypatch) -> None:
    _app()
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)
    source = tmp_path / "source.txt"
    source.write_text("hello", encoding="utf-8")
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    page = BackupTaskPage(BackupJobManager(store, device_id="device-1"))

    page.add_sources([str(source)])
    page.create_job()
    page.jobs_table.selectRow(0)
    page.transition_selected_job("running")
    page.transition_selected_job("paused")
    page.transition_selected_job("running")
    page.transition_selected_job("canceled")

    assert page.jobs_table.item(0, 1).text() == "已取消"
    with store.connect() as conn:
        job = conn.execute("SELECT status, data_version FROM backup_jobs").fetchone()
        assert job["status"] == "canceled"
        assert job["data_version"] == 5
        assert conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0] == 5


def test_source_mapping_page_renders_rows_without_local_path_leak(tmp_path, monkeypatch) -> None:
    _app()
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)
    secret_source = tmp_path / "secret-folder" / "secret.txt"
    secret_source.parent.mkdir()
    secret_source.write_text("hello", encoding="utf-8")
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    manager = BackupJobManager(store, device_id="device-1")
    created = manager.create_job([str(secret_source)], job_name="ui mapping")
    BackupScanner(store, device_id="device-1").scan_job(created.job.backup_job_id)
    ContentDedupeIndexer(store, device_id="device-1").build_job_index(created.job.backup_job_id)

    page = SourceMappingPage(store)
    page.job_id_input.setText(created.job.backup_job_id)
    page.refresh_mapping()

    table_text = _table_text(page.mapping_table)
    assert "secret.txt" in table_text
    assert str(secret_source) not in table_text
    assert str(tmp_path) not in table_text


def test_remote_reconcile_page_applies_confirmed_safe_repair(tmp_path, monkeypatch) -> None:
    _app()
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)
    db_path = tmp_path / "backup_state.sqlite3"
    store = SQLiteClientStore(db_path)
    store.migrate()
    remote_path = "/apps/app/backups/2026/06/08/device-1/job-1/archives/archive.7z"
    _insert_remote_object(store, remote_path=remote_path)
    monkeypatch.setattr(main_window, "BaiduCloudClient", _FakeCloudClient)
    monkeypatch.setattr(main_window, "BaiduAuthWorkflow", _FakeWorkflow)
    monkeypatch.setattr(main_window, "BaiduNetdiskClient", _FakeBaiduNetdiskClient)

    page = RemoteReconcilePage(
        store,
        cloud_api_base_url="https://backup.baichengedu.com",
        device_token="secret-device-token",
        device_id="device-1",
    )
    page.scope_value_input.setText("job-1")
    page.account_id_input.setText("account-1")
    page.password_input.setText("runtime-secret")
    page.run_reconcile()

    assert page.findings_table.rowCount() == 1
    assert page.findings_table.item(0, 0).text() == "remote_size_mismatch"
    page.confirm_input.setText("APPLY_REMOTE_REPAIR")
    page.apply_selected_repairs()

    with store.connect() as conn:
        remote = conn.execute("SELECT size_bytes, md5, fs_id, data_version FROM remote_objects").fetchone()
        outbox_count = conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0]
    assert remote["size_bytes"] == 12
    assert remote["md5"] == "c" * 32
    assert remote["fs_id"] == 202
    assert remote["data_version"] == 2
    assert outbox_count == 2
    assert remote_path not in _table_text(page.findings_table)


def test_restore_page_restores_selected_candidate_without_path_leak(tmp_path, monkeypatch) -> None:
    _app()
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)
    source = tmp_path / "secret-folder" / "secret.txt"
    source.parent.mkdir()
    source.write_text("payload", encoding="utf-8")
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    created = BackupJobManager(store, device_id="device-1").create_job([str(source)], job_name="restore ui")
    BackupPipeline(
        store=store,
        device_id="device-1",
        baidu_client=FakeBaiduForPipeline(),
        cloud_client=FakeCloudForPipeline(),
    ).run_job(
        created.job.backup_job_id,
        BackupPipelineOptions(
            cache_root=tmp_path / "cache",
            password="Test123456789",
            account_id="account-1",
            run_upload=True,
            sync_outbox=True,
            reconcile_remote=True,
            now="2026-06-08T21:10:00Z",
        ),
    )
    target_root = tmp_path / "restored"

    page = RestorePage(store, device_id="device-1", cache_root=str(tmp_path / "cache"))
    page.job_id_input.setText(created.job.backup_job_id)
    page.refresh_candidates()

    assert page.candidates_table.rowCount() == 1
    table_text = _table_text(page.candidates_table)
    assert "secret.txt" in table_text
    assert str(source) not in table_text
    assert str(tmp_path) not in table_text

    page.candidates_table.selectRow(0)
    page.target_root_input.setText(str(target_root))
    page.password_input.setText("Test123456789")
    page.apply_restore()

    assert (target_root / "secret.txt").read_text(encoding="utf-8") == "payload"
    assert store.list_restore_records(created.job.backup_job_id)[0]["restore_status"] == "restored"


def _table_text(table) -> str:  # type: ignore[no-untyped-def]
    values: list[str] = []
    for row in range(table.rowCount()):
        for col in range(table.columnCount()):
            item = table.item(row, col)
            if item is not None:
                values.append(item.text())
    return "\n".join(values)


class _FakeCloudClient:
    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc: object) -> None:
        pass


class _FakeWorkflow:
    def __init__(self, cloud) -> None:  # type: ignore[no-untyped-def]
        self.cloud = cloud

    def decrypt_password_token(self, account_id: str, *, authorization_password: str):
        assert account_id == "account-1"
        assert authorization_password == "runtime-secret"
        return _FakeDecrypted()

    def load_accounts(self):
        return []


class _FakeDecrypted:
    token = type("Token", (), {"access_token": "secret-access-token"})()


class _FakeBaiduNetdiskClient:
    def __init__(self, access_token: str, *, timeout: float = 120.0) -> None:
        del timeout
        assert access_token == "secret-access-token"

    def __enter__(self):
        return self

    def __exit__(self, *_exc: object) -> None:
        pass

    def list_all(self, *, remote_path: str, start: int = 0, limit: int = 1000, recursion: bool = True, web: bool = False):
        del start, limit, recursion, web
        assert remote_path == "/apps/app/backups/2026/06/08/device-1/job-1"
        return BaiduFileListResult(
            errno=0,
            items=(
                BaiduFileItem(
                    fs_id=202,
                    path="/apps/app/backups/2026/06/08/device-1/job-1/archives/archive.7z",
                    server_filename="archive.7z",
                    isdir=False,
                    size=12,
                    md5="c" * 32,
                ),
            ),
        )


def _insert_remote_object(store: SQLiteClientStore, *, remote_path: str) -> None:
    with store.transaction() as conn:
        payload = build_version_fields(
            entity_payload={
                "remote_object_id": "remote-archive",
                "entity_id": "remote_object_archive",
                "object_type": "archive",
                "job_id": "job-1",
                "device_id": "device-1",
                "archive_id": "archive-1",
                "archive_sha256": "a" * 64,
                "remote_path": remote_path,
                "size_bytes": 10,
                "md5": "b" * 32,
                "sha256": "a" * 64,
                "fs_id": 101,
                "status": "remote_created",
                "created_at": "2026-06-08T19:00:00Z",
            },
            updated_by_device_id="device-1",
            now="2026-06-08T19:00:00Z",
        )
        store.put_remote_object(conn, payload)
