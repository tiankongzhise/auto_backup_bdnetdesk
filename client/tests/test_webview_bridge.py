from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from auto_backup_client.baidu.models import EntitySummary, RevisionSummary
from auto_backup_client.device_credentials import DeviceCredentials
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
    device_id = kwargs.pop("device_id", "device-test")
    return AutoBackupWebviewBridge(settings=settings, store=store, device_id=device_id, **kwargs)


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


def test_bridge_resolves_local_device_credentials_into_runtime_settings(tmp_path) -> None:
    settings = ClientSettings(
        cloud_api_base_url="https://backup.example.test",
        device_token="",
        local_data_dir=str(tmp_path / "data"),
        local_sqlite_path=str(tmp_path / "data" / "state.sqlite3"),
        local_cache_dir=str(tmp_path / "cache"),
    )

    def resolver(**_kwargs):
        return (
            DeviceCredentials(
                cloud_api_base_url=settings.cloud_api_base_url,
                device_id="device-from-store",
                device_token="token-from-store",
            ),
            "本机 DPAPI 凭据",
        )

    bridge = AutoBackupWebviewBridge(settings=settings, device_credentials_resolver=resolver)
    bridge._try_accounts_summary = lambda: {"available": True, "selected_account_id": None, "items": []}

    assert bridge.settings.device_token == "token-from-store"
    assert bridge.device_id == "device-from-store"
    state = bridge.get_app_state()["data"]
    assert state["app"]["device_token_available"] is True
    assert state["app"]["device_credential_source"] == "本机 DPAPI 凭据"


def test_app_state_uses_comparable_device_id_hint_instead_of_hash_prefix(tmp_path) -> None:
    device_id = "dev_edae23f2-1bfb-f26e-654f-ea4d8fbb5d10"
    bridge = _bridge(tmp_path, device_id=device_id)
    bridge._try_accounts_summary = lambda: {"available": True, "selected_account_id": None, "items": []}

    state = bridge.get_app_state()["data"]

    assert state["app"]["device_id_hint"] == "dev_edae23f2...5d10"
    assert state["app"]["device_id_hint"] != "16da66855e"


def test_bridge_reports_device_credential_recovery_error_without_blocking_ui(tmp_path) -> None:
    settings = ClientSettings(
        cloud_api_base_url="https://backup.example.test",
        device_token="",
        local_data_dir=str(tmp_path / "data"),
        local_sqlite_path=str(tmp_path / "data" / "state.sqlite3"),
        local_cache_dir=str(tmp_path / "cache"),
    )

    def resolver(**_kwargs):
        raise RuntimeError("local credential store unavailable")

    bridge = AutoBackupWebviewBridge(settings=settings, device_credentials_resolver=resolver)

    state = bridge.get_app_state()["data"]
    assert state["app"]["device_token_available"] is False
    assert state["app"]["device_credential_source"] == "加载失败"
    assert state["app"]["device_id_resolved"] is False
    assert "local credential store unavailable" in state["app"]["device_credential_error"]


def test_bridge_rejects_write_operations_when_device_id_is_unresolved(tmp_path) -> None:
    settings = ClientSettings(
        cloud_api_base_url="https://backup.example.test",
        device_token="",
        local_data_dir=str(tmp_path / "data"),
        local_sqlite_path=str(tmp_path / "data" / "state.sqlite3"),
        local_cache_dir=str(tmp_path / "cache"),
    )

    def resolver(**_kwargs):
        raise RuntimeError("device id unavailable")

    bridge = AutoBackupWebviewBridge(settings=settings, device_credentials_resolver=resolver)

    response = bridge.create_job("blocked", [{"path": str(tmp_path)}])

    assert response["ok"] is False
    assert "Device ID" in response["error"]["message"]


def test_pipeline_options_from_api_preserves_upload_parameters(tmp_path) -> None:
    bridge = _bridge(tmp_path)

    options = bridge._pipeline_options_from_api(
        account_id="account-1",
        archive_password="ArchivePassword123",
        options={
            "root_dir": "/apps/custom/backups",
            "run_upload": True,
            "check_quota": False,
            "sync_outbox": False,
            "reconcile_remote": True,
            "enforce_cache_budget": True,
            "cleanup_cache_artifacts": True,
            "part_size": 16 * 1024 * 1024,
            "max_archive_size_bytes": 10 * 1024 * 1024 * 1024,
        },
    )

    assert options.root_dir == "/apps/custom/backups"
    assert options.part_size == 16 * 1024 * 1024
    assert options.max_archive_size_bytes == 10 * 1024 * 1024 * 1024
    assert options.check_quota is False
    assert options.sync_outbox is False
    assert options.reconcile_remote is True
    assert options.enforce_cache_budget is True
    assert options.cleanup_cache_artifacts is True


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


def test_choose_sources_mixed_combines_file_and_directory_dialogs(tmp_path, monkeypatch) -> None:
    source_file = tmp_path / "a.txt"
    source_file.write_text("demo", encoding="utf-8")
    source_dir = tmp_path / "folder"
    source_dir.mkdir()

    class StubWindow:
        def create_file_dialog(self, **kwargs):
            if kwargs["dialog_type"] is StubWebview.OPEN_DIALOG:
                return [str(source_file)]
            if kwargs["dialog_type"] is StubWebview.FOLDER_DIALOG:
                return [str(source_dir)]
            return []

    class StubWebview:
        OPEN_DIALOG = object()
        FOLDER_DIALOG = object()

    monkeypatch.setitem(__import__("sys").modules, "webview", StubWebview())
    bridge = _bridge(tmp_path)
    bridge.set_window(StubWindow())

    response = bridge.choose_sources("mixed")

    assert response["ok"] is True
    sources = response["data"]["sources"]
    assert [item["source_type"] for item in sources] == ["file", "directory"]
    assert all("source_token" in item for item in sources)


def test_cleanup_confirmation_error_is_exposed_as_redacted_operation(tmp_path) -> None:
    bridge = _bridge(tmp_path, run_operations_inline=True)

    response = bridge.apply_cleanup(["missing"], {"dry_run": False, "confirm_text": "WRONG"})

    assert response["ok"] is True
    operation = response["data"]["operation"]
    assert operation["status"] == "failed"
    assert "cleanup" in operation["error"]["type"].lower()


def test_cleanup_permanent_delete_requires_advanced_enabled(tmp_path) -> None:
    bridge = _bridge(tmp_path, run_operations_inline=True)

    response = bridge.apply_cleanup(
        ["missing"],
        {
            "dry_run": False,
            "method": "permanent_delete",
            "confirm_text": "CLEANUP_SOURCES",
            "permanent_confirm_text": "PERMANENT_DELETE_SOURCES",
        },
    )

    assert response["ok"] is True
    operation = response["data"]["operation"]
    assert operation["status"] == "failed"
    assert "ValueError" in operation["error"]["type"]


def test_get_operation_reports_missing_operation_as_safe_error(tmp_path) -> None:
    bridge = _bridge(tmp_path)

    response = bridge.get_operation("not-found")

    assert response["ok"] is False
    assert "\\" not in response["error"]["message"]


def test_baidu_account_dto_uses_summaries_not_full_device_or_uid(tmp_path) -> None:
    bridge = _bridge(tmp_path)
    bridge._has_local_kdf_record = lambda account_id: account_id == "account-1"
    account = SimpleNamespace(
        account_id="account-1",
        baidu_uk="1234567890123456",
        baidu_uid="uid-secret-abcdef",
        device_id="device-secret-abcdef",
        current_device=True,
        display_name="测试账号",
        selected=True,
        token_valid=True,
        token_expires_at=SimpleNamespace(isoformat=lambda: "2026-06-16T00:00:00+00:00"),
        last_verify_status="valid",
    )

    dto = bridge._baidu_account_to_dto(account)

    assert dto["device_hint"] == "devi...cdef"
    assert dto["uid_hint"] == "uid-...cdef"
    assert dto["local_kdf_available"] is True
    dumped = json.dumps(dto, ensure_ascii=False)
    assert "device-secret-abcdef" not in dumped
    assert "uid-secret-abcdef" not in dumped


def test_baidu_account_dto_uses_comparable_device_id_hint_for_stable_ids(tmp_path) -> None:
    bridge = _bridge(tmp_path)
    bridge._has_local_kdf_record = lambda _account_id: False
    account = SimpleNamespace(
        account_id="account-1",
        baidu_uk="1234567890123456",
        baidu_uid="uid-secret-abcdef",
        device_id="dev_edae23f2-1bfb-f26e-654f-ea4d8fbb5d10",
        current_device=True,
        display_name="测试账号",
        selected=True,
        token_valid=True,
        token_expires_at=SimpleNamespace(isoformat=lambda: "2026-06-16T00:00:00+00:00"),
        last_verify_status="valid",
    )

    dto = bridge._baidu_account_to_dto(account)

    assert dto["device_hint"] == "dev_edae23f2...5d10"
    assert dto["device_hint"] != "16da66855e"


def test_verify_baidu_token_returns_safe_success_dto(tmp_path, monkeypatch) -> None:
    bridge = _bridge(tmp_path)
    decrypted = SimpleNamespace(
        encrypted=SimpleNamespace(
            token_version=7,
            token_expires_at=SimpleNamespace(isoformat=lambda: "2026-06-16T00:00:00+00:00"),
        )
    )

    monkeypatch.setattr(bridge, "_with_auth_workflow", lambda func: func(SimpleNamespace(decrypt_password_token=lambda **_kwargs: decrypted)))

    response = bridge.verify_baidu_token("account-1", "secret-password")

    assert response["ok"] is True
    verification = response["data"]["verification"]
    assert verification["valid"] is True
    dumped = json.dumps(verification, ensure_ascii=False)
    assert "secret-password" not in dumped


def test_cloud_sync_summary_dto_matches_entity_summary_model(tmp_path, monkeypatch) -> None:
    bridge = _bridge(tmp_path)
    summary = EntitySummary(
        entity_id="backup_jobs:job-1",
        entity_type="backup_jobs",
        data_version=3,
        revision_id="018fe9c0-0000-7000-8000-000000000001",
        canonical_record_sha256="a" * 64,
        updated_by_device_id="dev_edae23f2-1bfb-f26e-654f-ea4d8fbb5d10",
        deleted_at=None,
        recent_revisions=(
            RevisionSummary(
                event_id="evt_018fe9c0_0001",
                revision_id="018fe9c0-0000-7000-8000-000000000001",
                data_version=3,
                apply_status="synced",
                canonical_record_sha256="b" * 64,
                created_at=datetime(2026, 6, 22, 8, 0, tzinfo=timezone.utc),
            ),
        ),
    )

    class FakeCloud:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get_entity_summary(self, entity_id: str) -> EntitySummary:
            assert entity_id == "backup_jobs:job-1"
            return summary

    monkeypatch.setattr(bridge, "_cloud_client", lambda: FakeCloud())

    response = bridge.get_cloud_sync_summary("backup_jobs:job-1")

    assert response["ok"] is True
    dto = response["data"]["summary"]
    assert dto["entity_id"] == "backup_jobs:job-1"
    assert dto["entity_type"] == "backup_jobs"
    assert dto["data_version"] == 3
    assert dto["revision_id"] == "018fe9c0-0000-7000-8000-000000000001"
    assert dto["revision_id_hint"] == "018f...0001"
    assert dto["canonical_record_sha256"] == "a" * 64
    assert dto["canonical_record_sha256_hint"] == "a" * 16
    assert dto["updated_by_device_id"] == "dev_edae23f2-1bfb-f26e-654f-ea4d8fbb5d10"
    assert dto["updated_by_device_hint"] == "dev_edae23f2...5d10"
    assert dto["recent_revisions"][0]["apply_status"] == "synced"
    assert dto["recent_revisions"][0]["canonical_record_sha256_hint"] == "b" * 16
    dumped = json.dumps(dto, ensure_ascii=False)
    assert "revision_count" not in dumped
    assert "latest_revision_id" not in dumped
