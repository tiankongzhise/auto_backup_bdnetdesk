from __future__ import annotations

import os
import shutil
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from auto_backup_client.backup_jobs import BackupJobManager
from auto_backup_client.backup_history_sync import DeviceBackupHistoryRefreshResult
from auto_backup_client.backup_pipeline import BackupPipeline, BackupPipelineOptions
from auto_backup_client.baidu.upload import BaiduFileItem, BaiduFileListResult
from auto_backup_client.dedupe_index import ContentDedupeIndexer
from auto_backup_client.scan_fingerprints import BackupScanner
from auto_backup_client.sqlite_store import SQLiteClientStore, build_version_fields
from auto_backup_client.ui import main_window
from auto_backup_client.ui.main_window import (
    BackupTaskPage,
    BackupTaskPageConfig,
    CloudSyncPage,
    MainWindow,
    MainWindowConfig,
    RemoteReconcilePage,
    RestorePage,
    SourceCleanupPage,
    SourceMappingPage,
)
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


def test_backup_task_page_single_add_source_entry_auto_detects_files_and_folders(tmp_path, monkeypatch) -> None:
    _app()
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)
    file_source = tmp_path / "source.txt"
    folder_source = tmp_path / "photos"
    file_source.write_text("hello", encoding="utf-8")
    folder_source.mkdir()
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    page = BackupTaskPage(BackupJobManager(store, device_id="device-1"))

    page.add_sources([str(file_source), str(folder_source), str(file_source)])
    page.create_job()

    with store.connect() as conn:
        sources = conn.execute("SELECT source_type, display_name FROM backup_sources ORDER BY source_seq").fetchall()
    assert page.add_sources_button.text() == "添加来源"
    assert [row["source_type"] for row in sources] == ["file", "directory"]
    assert [row["display_name"] for row in sources] == ["source.txt", "photos"]


def test_backup_task_page_source_picker_adds_files_and_folders_together(tmp_path, monkeypatch) -> None:
    _app()
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)
    file_source = tmp_path / "source.txt"
    folder_source = tmp_path / "photos"
    file_source.write_text("hello", encoding="utf-8")
    folder_source.mkdir()
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    page = BackupTaskPage(BackupJobManager(store, device_id="device-1"))

    page.choose_sources()
    assert page._source_picker is not None
    page._source_picker.sources_selected.emit([str(file_source), str(folder_source)])
    page.create_job()

    with store.connect() as conn:
        sources = conn.execute("SELECT source_type, display_name FROM backup_sources ORDER BY source_seq").fetchall()
    assert [row["source_type"] for row in sources] == ["file", "directory"]
    assert [row["display_name"] for row in sources] == ["source.txt", "photos"]


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
        assert conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0] == 6


def test_backup_task_page_continue_is_enabled_for_imported_running_job(tmp_path, monkeypatch) -> None:
    _app()
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)
    source = tmp_path / "source.txt"
    source.write_text("hello", encoding="utf-8")
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    manager = BackupJobManager(store, device_id="device-1")
    created = manager.create_job([str(source)], job_name="cloud running")
    manager.transition_job(created.job.backup_job_id, "running")

    page = BackupTaskPage(manager)
    page.jobs_table.selectRow(0)

    assert page.start_button.isEnabled() is False
    assert page.resume_button.isEnabled() is True


def test_backup_task_page_start_runs_real_pipeline_without_sensitive_status_leak(tmp_path, monkeypatch) -> None:
    _app()
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)
    source = tmp_path / "secret-folder" / "source.txt"
    source.parent.mkdir()
    source.write_text("payload", encoding="utf-8")
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    fake_cloud = _FakePipelineCloudClient()
    fake_baidu_clients: list[_FakePipelineBaiduClient] = []
    monkeypatch.setattr(main_window, "BaiduCloudClient", lambda *args, **kwargs: fake_cloud)
    monkeypatch.setattr(main_window, "BaiduAuthWorkflow", _FakePipelineWorkflow)

    def fake_baidu_factory(*args, **kwargs):  # type: ignore[no-untyped-def]
        client = _FakePipelineBaiduClient(*args, **kwargs)
        fake_baidu_clients.append(client)
        return client

    monkeypatch.setattr(main_window, "BaiduNetdiskClient", fake_baidu_factory)
    page = BackupTaskPage(
        BackupJobManager(store, device_id="device-1"),
        BackupTaskPageConfig(
            cloud_api_base_url="https://backup.baichengedu.com",
            device_token="secret-device-token",
            device_id="device-1",
            cache_root=str(tmp_path / "cache"),
        ),
    )
    page._thread_pool = _InlineThreadPool()  # type: ignore[assignment]
    status_messages: list[str] = []
    page.status_changed.connect(status_messages.append)
    finished_jobs: list[str] = []
    page.backup_finished.connect(finished_jobs.append)

    page.add_sources([str(source)])
    page.job_name_input.setText("ui real backup")
    page.create_job()
    job_id = page.jobs_table.item(0, 0).data(main_window.Qt.ItemDataRole.UserRole)
    page.jobs_table.selectRow(0)
    page.archive_password_input.setText("Test123456789")
    page.authorization_password_input.setText("runtime-secret")
    custom_cache = tmp_path / "custom-cache"
    page.cache_root_input.setText(str(custom_cache))
    page.cache_budget_checkbox.setChecked(False)
    page.start_selected_job()

    with store.connect() as conn:
        job = conn.execute("SELECT status, sync_status FROM backup_jobs WHERE backup_job_id = ?", (job_id,)).fetchone()
        archives = conn.execute("SELECT COUNT(*) FROM archives WHERE job_id = ?", (job_id,)).fetchone()[0]
        uploads = conn.execute("SELECT COUNT(*) FROM upload_sessions WHERE job_id = ?", (job_id,)).fetchone()[0]
        remotes = conn.execute("SELECT COUNT(*) FROM remote_objects WHERE job_id = ?", (job_id,)).fetchone()[0]
    assert job["status"] == "completed"
    assert job["sync_status"] == "synced"
    assert archives == 1
    assert uploads == 1
    assert remotes == 3
    assert fake_cloud.synced_event_ids
    assert fake_baidu_clients[0].uploaded_partseqs
    assert page.archive_password_input.text() == ""
    assert page.authorization_password_input.text() == ""
    assert page._config.cache_root == str(custom_cache)
    assert finished_jobs == [job_id]
    assert page.jobs_table.item(0, 1).text() == "已完成"
    combined_status = "\n".join(status_messages)
    assert str(source) not in combined_status
    assert str(tmp_path) not in combined_status
    assert "runtime-secret" not in combined_status
    assert "Test123456789" not in combined_status


def test_main_window_starts_and_syncs_restore_cache_root(tmp_path, monkeypatch) -> None:
    _app()
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)
    window = MainWindow(
        MainWindowConfig(
            cloud_api_base_url="https://backup.baichengedu.com",
            device_token="secret-device-token",
            device_id="device-1",
            sqlite_path=str(tmp_path / "backup_state.sqlite3"),
            cache_root=str(tmp_path / "cache"),
        )
    )

    assert hasattr(window._restore_page, "set_cache_root")
    new_cache = str(tmp_path / "new-cache")
    window._backup_page.cache_root_changed.emit(new_cache)

    assert window._restore_page._cache_root == new_cache
    assert not hasattr(window._cleanup_page, "set_cache_root")
    window.close()


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
    page.select_job(created.job.backup_job_id)
    page.refresh_mapping()

    table_text = _table_text(page.mapping_table)
    assert "secret.txt" in table_text
    assert str(secret_source) not in table_text
    assert str(tmp_path) not in table_text


def test_source_mapping_and_cleanup_refresh_device_history_before_listing(tmp_path, monkeypatch) -> None:
    _app()
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    mapping_refresher = _FakeHistoryRefresher()
    cleanup_refresher = _FakeHistoryRefresher()

    mapping_page = SourceMappingPage(store, history_refresher=mapping_refresher)
    cleanup_page = SourceCleanupPage(store, device_id="device-1", history_refresher=cleanup_refresher)
    mapping_page.refresh_mapping()
    cleanup_page.refresh_candidates()

    assert mapping_refresher.calls == 2
    assert cleanup_refresher.calls == 2
    assert mapping_page.job_filter.currentText() == "全部最近记录"
    assert cleanup_page.job_filter.currentText() == "全部最近记录"


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
    assert page.findings_table.wordWrap() is True
    assert "remote_objects/upload_sessions" in page.summary_label.text()
    assert "list/listall" in page.summary_label.text()
    assert page.findings_table.item(0, 0).text() == "remote_size_mismatch"
    assert page.findings_table.item(0, 2).toolTip() == page.findings_table.item(0, 2).text()
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
    page.select_job(created.job.backup_job_id)
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


def test_restore_page_downloads_remote_archive_when_local_cache_is_missing(tmp_path, monkeypatch) -> None:
    _app()
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)
    source = tmp_path / "secret-folder" / "download-me.txt"
    source.parent.mkdir()
    source.write_text("remote payload", encoding="utf-8")
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    created = BackupJobManager(store, device_id="device-1").create_job([str(source)], job_name="remote restore ui")
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
            now="2026-06-08T21:30:00Z",
        ),
    )
    original_archive = Path(store.list_archives(created.job.backup_job_id)[0]["local_archive_path"])
    remote_archive = tmp_path / "remote-copy.7z"
    shutil.copy2(original_archive, remote_archive)
    original_archive.unlink()
    monkeypatch.setattr(main_window, "BaiduCloudClient", _FakeCloudClient)
    monkeypatch.setattr(main_window, "BaiduAuthWorkflow", _FakeWorkflow)
    monkeypatch.setattr(
        main_window,
        "BaiduNetdiskClient",
        lambda *args, **kwargs: _FakeDownloadBaiduNetdiskClient(remote_archive, *args, **kwargs),
    )
    target_root = tmp_path / "restored"

    page = RestorePage(
        store,
        device_id="device-1",
        cache_root=str(tmp_path / "cache"),
        cloud_api_base_url="https://backup.baichengedu.com",
        device_token="secret-device-token",
    )
    page.select_job(created.job.backup_job_id)
    page.refresh_candidates()

    assert page.candidates_table.item(0, 0).text() == "needs_download"
    page.candidates_table.selectRow(0)
    page.target_root_input.setText(str(target_root))
    page.password_input.setText("Test123456789")
    page.account_id_input.setText("account-1")
    page.authorization_password_input.setText("runtime-secret")
    page.apply_restore()

    assert (target_root / "download-me.txt").read_text(encoding="utf-8") == "remote payload"
    records = store.list_restore_records(created.job.backup_job_id)
    assert records[0]["restore_status"] == "restored"
    assert records[0]["archive_source"] == "downloaded"


def test_restore_page_refresh_imports_device_history_before_listing(tmp_path, monkeypatch) -> None:
    _app()
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    refresher = _FakeHistoryRefresher()

    page = RestorePage(
        store,
        device_id="device-1",
        cache_root=str(tmp_path / "cache"),
        cloud_api_base_url="https://backup.baichengedu.com",
        device_token="secret-device-token",
        history_refresher=refresher,
    )
    page.refresh_candidates()

    assert refresher.calls == 2


def test_source_cleanup_page_hides_permanent_delete_and_requires_selection(tmp_path, monkeypatch) -> None:
    _app()
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)
    source = tmp_path / "secret-folder" / "cleanup-me.txt"
    source.parent.mkdir()
    source.write_text("payload", encoding="utf-8")
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    created = BackupJobManager(store, device_id="device-1").create_job([str(source)], job_name="cleanup ui")
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
            now="2026-06-08T21:40:00Z",
        ),
    )

    page = SourceCleanupPage(store, device_id="device-1")
    status_messages: list[str] = []
    page.status_changed.connect(status_messages.append)
    page.select_job(created.job.backup_job_id)
    page.refresh_candidates()

    assert page.candidates_table.rowCount() == 1
    assert page.method_combo.findData("permanent_delete") == -1
    page.advanced_cleanup_checkbox.setChecked(True)
    assert page.method_combo.findData("permanent_delete") >= 0

    page.confirm_input.setText("CLEANUP_SOURCES")
    page.apply_cleanup(dry_run=True)

    assert status_messages[-1] == "请先选择要清理的来源。"
    assert store.list_source_cleanup_records(created.job.backup_job_id) == []


def test_source_cleanup_page_shows_exact_confirmation_phrase(tmp_path, monkeypatch) -> None:
    _app()
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)
    source = tmp_path / "secret-folder" / "cleanup-me.txt"
    source.parent.mkdir()
    source.write_text("payload", encoding="utf-8")
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    created = BackupJobManager(store, device_id="device-1").create_job([str(source)], job_name="cleanup ui")
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
            now="2026-06-08T21:42:00Z",
        ),
    )
    page = SourceCleanupPage(store, device_id="device-1")
    status_messages: list[str] = []
    page.status_changed.connect(status_messages.append)
    page.select_job(created.job.backup_job_id)
    page.refresh_candidates()

    page.candidates_table.selectRow(0)
    page.confirm_input.setText("CLEANUO_SOURCES")
    page.apply_cleanup(dry_run=False)

    assert status_messages[-1] == "确认短语应为 CLEANUP_SOURCES"
    assert store.list_source_cleanup_records(created.job.backup_job_id) == []


def test_cloud_sync_page_renders_local_and_cloud_summary(tmp_path, monkeypatch) -> None:
    _app()
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_window, "BaiduCloudClient", _FakeCloudSummaryClient)
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    source = tmp_path / "source.txt"
    source.write_text("payload", encoding="utf-8")
    created = BackupJobManager(store, device_id="device-1").create_job([str(source)], job_name="sync ui")

    page = CloudSyncPage(
        store,
        cloud_api_base_url="https://backup.baichengedu.com",
        device_token="secret-device-token",
        device_id="device-1",
    )

    assert "sync_outbox" in page.summary_label.text()
    assert page.status_table.rowCount() >= 1
    page.entity_id_input.setText(created.job.entity_id)
    page.query_cloud_summary()

    assert "entity_type" in _table_text(page.cloud_summary_table)
    assert "backup_jobs" in _table_text(page.cloud_summary_table)


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
    def __init__(self, cloud, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        del args, kwargs
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


class _FakeDownloadBaiduNetdiskClient:
    def __init__(self, archive_path: Path, access_token: str, *, timeout: float = 120.0) -> None:
        del timeout
        self.archive_path = archive_path
        assert access_token == "secret-access-token"

    def __enter__(self):
        return self

    def __exit__(self, *_exc: object) -> None:
        pass

    def file_metas(self, fs_ids, *, dlink: bool = False):  # type: ignore[no-untyped-def]
        assert tuple(fs_ids)
        assert dlink is True
        fs_id = int(tuple(fs_ids)[0])
        return type(
            "Metas",
            (),
            {
                "items": (
                    type(
                        "Meta",
                        (),
                        {
                            "fs_id": fs_id,
                            "dlink": "https://d.pcs.baidu.com/file/archive?sign=fake",
                            "size": self.archive_path.stat().st_size,
                        },
                    )(),
                )
            },
        )()

    def download_dlink(self, dlink: str, target_path: str | Path) -> None:
        assert dlink.startswith("https://d.pcs.baidu.com/file/archive")
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.archive_path, target)


class _InlineThreadPool:
    def start(self, worker) -> None:  # type: ignore[no-untyped-def]
        worker.run()


class _FakePipelineCloudClient(FakeCloudForPipeline):
    def __enter__(self):
        return self

    def __exit__(self, *_exc: object) -> None:
        pass


class _FakePipelineWorkflow:
    def __init__(self, cloud, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        del args, kwargs
        self.cloud = cloud

    def load_accounts(self):
        return [SimpleNamespace(account_id="account-1", selected=True)]

    def decrypt_password_token(self, account_id: str, *, authorization_password: str):
        assert account_id == "account-1"
        assert authorization_password == "runtime-secret"
        return SimpleNamespace(
            encrypted=SimpleNamespace(account_id="account-1"),
            token=SimpleNamespace(access_token="secret-access-token"),
        )


class _FakePipelineBaiduClient(FakeBaiduForPipeline):
    def __init__(self, access_token: str = "", *, timeout: float = 120.0) -> None:
        del timeout
        super().__init__()
        assert access_token == "secret-access-token"

    def __enter__(self):
        return self

    def __exit__(self, *_exc: object) -> None:
        pass


class _FakeCloudSummaryClient:
    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        del args, kwargs

    def __enter__(self):
        return self

    def __exit__(self, *_exc: object) -> None:
        pass

    def get_entity_summary(self, entity_id: str):
        return SimpleNamespace(
            entity_id=entity_id,
            entity_type="backup_jobs",
            data_version=1,
            revision_id="rev_" + "a" * 12,
            canonical_record_sha256="b" * 64,
            updated_by_device_id="device-1",
            deleted_at=None,
            recent_revisions=(
                SimpleNamespace(apply_status="synced", data_version=1, revision_id="rev_" + "c" * 12),
            ),
        )


class _FakeHistoryRefresher:
    def __init__(self, result: DeviceBackupHistoryRefreshResult | None = None) -> None:
        self.result = result or DeviceBackupHistoryRefreshResult(attempted=True, imported_count=1, skipped_count=0)
        self.calls = 0

    def refresh(self) -> DeviceBackupHistoryRefreshResult:
        self.calls += 1
        return self.result


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
