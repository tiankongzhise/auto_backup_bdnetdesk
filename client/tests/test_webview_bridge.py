from __future__ import annotations

import json
from pathlib import Path

from auto_backup_client.settings import ClientSettings
from auto_backup_client.sqlite_store import SQLiteClientStore
from auto_backup_client.webview_bridge import AutoBackupWebviewBridge


def _settings(tmp_path: Path) -> ClientSettings:
    return ClientSettings(
        cloud_api_base_url="https://backup.example.test",
        device_token="device-token-test",
        local_data_dir=str(tmp_path / "data"),
        local_sqlite_path=str(tmp_path / "data" / "state.sqlite3"),
        local_cache_dir=str(tmp_path / "cache"),
    )


def _bridge(tmp_path: Path, **kwargs) -> AutoBackupWebviewBridge:
    settings = _settings(tmp_path)
    Path(settings.local_data_dir).mkdir(parents=True, exist_ok=True)
    store = SQLiteClientStore(settings.local_sqlite_path)
    store.migrate()
    return AutoBackupWebviewBridge(settings=settings, store=store, device_id="device-test", **kwargs)


def test_create_job_returns_redacted_source_dto(tmp_path) -> None:
    source = tmp_path / "secret-folder"
    source.mkdir()
    bridge = _bridge(tmp_path)

    response = bridge.create_job("每日备份", [{"path": str(source)}])

    assert response["ok"] is True
    payload = response["data"]["job"]
    assert payload["name"] == "每日备份"
    dumped = json.dumps(payload, ensure_ascii=False)
    assert str(source) not in dumped
    assert "path_digest" in dumped


def test_start_job_returns_operation_without_password_leak(tmp_path) -> None:
    bridge = _bridge(
        tmp_path,
        run_operations_inline=True,
        backup_runner=lambda operation: {"stage": operation.stage, "message": "done"},
    )

    response = bridge.start_job(
        "job-test",
        {"archive_password": "ArchivePassword123", "authorization_password": "AuthPassword456"},
        {},
    )

    assert response["ok"] is True
    operation = response["data"]["operation"]
    assert operation["status"] == "completed"
    dumped = json.dumps(operation, ensure_ascii=False)
    assert "ArchivePassword123" not in dumped
    assert "AuthPassword456" not in dumped


def test_choose_sources_detects_file_and_directory_with_stub_window(tmp_path, monkeypatch) -> None:
    source_file = tmp_path / "a.txt"
    source_file.write_text("demo", encoding="utf-8")
    source_dir = tmp_path / "folder"
    source_dir.mkdir()

    class StubWindow:
        def create_file_dialog(self, **_kwargs):
            return [str(source_file), str(source_dir)]

    class StubWebview:
        OPEN_DIALOG = object()
        FOLDER_DIALOG = object()

    monkeypatch.setitem(__import__("sys").modules, "webview", StubWebview())
    bridge = _bridge(tmp_path)
    bridge.set_window(StubWindow())

    response = bridge.choose_sources()

    assert response["ok"] is True
    sources = response["data"]["sources"]
    assert [item["source_type"] for item in sources] == ["file", "directory"]
    dumped = json.dumps(sources, ensure_ascii=False)
    assert "path_digest" in dumped
    assert "source_token" in dumped
    assert str(source_file) not in dumped
    assert str(source_dir) not in dumped


def test_cleanup_confirmation_error_is_exposed_as_redacted_operation(tmp_path) -> None:
    bridge = _bridge(tmp_path, run_operations_inline=True)

    response = bridge.apply_cleanup(["missing"], {"dry_run": False, "confirm_text": "WRONG"})

    assert response["ok"] is True
    operation = response["data"]["operation"]
    assert operation["status"] == "failed"
    assert "cleanup" in operation["error"]["type"].lower()


def test_get_operation_reports_missing_operation_as_safe_error(tmp_path) -> None:
    bridge = _bridge(tmp_path)

    response = bridge.get_operation("not-found")

    assert response["ok"] is False
    assert "\\" not in response["error"]["message"]
