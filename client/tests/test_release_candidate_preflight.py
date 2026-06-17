from __future__ import annotations

from pathlib import Path

from auto_backup_client.release_build import APP_NAME
from auto_backup_client.release_candidate_preflight import (
    PreflightFinding,
    WebView2RuntimeStatus,
    preflight_release_candidate,
    main,
)
from auto_backup_client.settings import ClientSettings


def test_release_candidate_preflight_passes_clean_pyinstaller_onedir(tmp_path) -> None:
    release_dir = _clean_release_dir(tmp_path)

    result = preflight_release_candidate(
        release_dir=release_dir,
        data_dir=tmp_path / "user-data",
        cache_dir=tmp_path / "user-cache",
    )

    assert result.passed is True
    assert result.checked_files >= 5
    assert result.webview2.label == "skipped"
    assert result.findings == ()


def test_release_candidate_preflight_detects_missing_boot_resources(tmp_path) -> None:
    release_dir = tmp_path / "dist" / APP_NAME
    release_dir.mkdir(parents=True)
    (release_dir / f"{APP_NAME}.exe").write_bytes(b"exe")

    result = preflight_release_candidate(release_dir=release_dir, data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")

    checks = {finding.check for finding in result.findings}
    assert "required_webui_asset" in checks
    assert "required_sqlite_migration" in checks
    assert "sqlite_migration_bundle" in checks


def test_release_candidate_preflight_rejects_runtime_artifacts_in_release_dir(tmp_path) -> None:
    release_dir = _clean_release_dir(tmp_path)
    (release_dir / "backup_state.sqlite3").write_bytes(b"sqlite")
    (release_dir / "credentials").mkdir()
    (release_dir / "var" / "data").mkdir(parents=True)
    (release_dir / "run.log").write_text("runtime log", encoding="utf-8")

    result = preflight_release_candidate(release_dir=release_dir, data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")

    checks = [finding.check for finding in result.findings]
    assert checks.count("blocked_runtime_file") >= 2
    assert "blocked_runtime_dir" in checks


def test_release_candidate_preflight_rejects_user_data_inside_release_dir(tmp_path) -> None:
    release_dir = _clean_release_dir(tmp_path)

    result = preflight_release_candidate(
        release_dir=release_dir,
        data_dir=release_dir / "data",
        cache_dir=release_dir / "_internal" / "cache",
    )

    checks = [finding.check for finding in result.findings]
    assert "data_dir" in checks
    assert "cache_dir" in checks


def test_release_candidate_preflight_reports_webview2_missing_when_requested(tmp_path) -> None:
    release_dir = _clean_release_dir(tmp_path)

    result = preflight_release_candidate(
        release_dir=release_dir,
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        check_webview2=True,
        webview2_detector=lambda: WebView2RuntimeStatus(
            checked=True,
            present=False,
            skipped=False,
            detail="not installed",
        ),
    )

    assert result.passed is False
    assert PreflightFinding("webview2_runtime", "WebView2", "not installed") in result.findings


def test_release_candidate_preflight_cli_outputs_safe_paths(tmp_path, capsys) -> None:
    release_dir = _clean_release_dir(tmp_path)
    secret_dir = release_dir / "very" / "deep" / "credentials"
    secret_dir.mkdir(parents=True)

    assert main(["--release-dir", str(release_dir), "--data-dir", str(tmp_path / "data"), "--cache-dir", str(tmp_path / "cache")]) == 1
    output = capsys.readouterr().out

    assert "release_candidate_preflight_passed: false" in output
    assert "blocked_runtime_dir" in output
    assert str(tmp_path) not in output


def test_client_settings_default_runtime_dirs_use_local_app_data() -> None:
    settings = ClientSettings.from_env({"LOCALAPPDATA": r"C:\Users\Alice\AppData\Local"})

    assert settings.local_data_dir == r"C:\Users\Alice\AppData\Local\auto_backup_bdnetdesk\data"
    assert settings.local_sqlite_path == r"C:\Users\Alice\AppData\Local\auto_backup_bdnetdesk\data\backup_state.sqlite3"
    assert settings.local_cache_dir == r"C:\Users\Alice\AppData\Local\auto_backup_bdnetdesk\cache"


def _clean_release_dir(tmp_path: Path) -> Path:
    release_dir = tmp_path / "dist" / APP_NAME
    internal = release_dir / "_internal"
    (internal / "webui" / "js" / "views").mkdir(parents=True)
    (internal / "migrations" / "sqlite").mkdir(parents=True)
    (release_dir / f"{APP_NAME}.exe").write_bytes(b"exe")
    for relative in (
        "webui/index.html",
        "webui/styles.css",
        "webui/js/api.js",
        "webui/js/app.js",
        "webui/js/render.js",
        "webui/js/state.js",
        "webui/js/views/settings.js",
        "migrations/sqlite/001_sync_outbox.sql",
        "migrations/sqlite/012_restore_history_sync_fields.sql",
    ):
        (internal / relative).write_text("-- bundled", encoding="utf-8")
    return release_dir
