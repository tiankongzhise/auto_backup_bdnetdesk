from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from auto_backup_client.backup_jobs import BackupJobManager
from auto_backup_client.sqlite_store import SQLiteClientStore
from auto_backup_client.ui.main_window import BackupTaskPage


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

