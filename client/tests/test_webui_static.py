from __future__ import annotations

from pathlib import Path


WEBUI = Path(__file__).resolve().parents[1] / "src" / "auto_backup_client" / "webui"


def test_webui_required_static_files_exist() -> None:
    expected = [
        "index.html",
        "styles.css",
        "js/api.js",
        "js/state.js",
        "js/app.js",
        "js/render.js",
        "js/views/dashboard.js",
        "js/views/jobs.js",
        "js/views/baidu.js",
        "js/views/restore.js",
        "js/views/cleanup.js",
        "js/views/reconcile.js",
        "js/views/settings.js",
    ]

    missing = [item for item in expected if not (WEBUI / item).is_file()]

    assert missing == []


def test_only_api_module_accesses_pywebview_bridge() -> None:
    offenders = []
    for path in (WEBUI / "js").rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        if "window.pywebview" in text and path.name != "api.js":
            offenders.append(path.relative_to(WEBUI).as_posix())

    assert offenders == []


def test_webui_does_not_persist_sensitive_state_in_browser_storage() -> None:
    forbidden = ("localStorage", "sessionStorage", "indexedDB")
    offenders = []
    for path in WEBUI.rglob("*"):
        if path.is_file() and path.suffix in {".html", ".js", ".css"}:
            text = path.read_text(encoding="utf-8")
            if any(term in text for term in forbidden):
                offenders.append(path.relative_to(WEBUI).as_posix())

    assert offenders == []


def test_webui_does_not_use_inner_html_sink() -> None:
    offenders = []
    for path in WEBUI.rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        if "innerHTML" in text:
            offenders.append(path.relative_to(WEBUI).as_posix())

    assert offenders == []


def test_jobs_page_exposes_single_source_picker() -> None:
    text = (WEBUI / "js" / "views" / "jobs.js").read_text(encoding="utf-8")

    assert 'button("添加来源"' in text
    assert 'class: "source-picker"' in text
    assert 'context.call("choose_sources", "mixed")' not in text
    assert 'chooseNativeSources("file")' in text
    assert 'chooseNativeSources("directory")' in text


def test_jobs_page_passes_baidu_upload_parameters_to_bridge() -> None:
    text = (WEBUI / "js" / "views" / "jobs.js").read_text(encoding="utf-8")

    assert "root_dir: rootDir.value.trim()" in text
    assert "part_size: Number(partSize.value)" in text
    assert "max_archive_size_bytes: Number(maxArchiveSize.value)" in text
    assert "check_quota: checkQuota.checked" in text
    assert "sync_outbox: syncOutbox.checked" in text
    assert "reconcile_remote: reconcileRemote.checked" in text
    assert "cleanup_cache_artifacts: cleanupCache.checked" in text


def test_baidu_page_exposes_device_credential_status() -> None:
    text = (WEBUI / "js" / "views" / "baidu.js").read_text(encoding="utf-8")

    assert "device_token_available" in text
    assert "device_credential_source" in text
    assert "device_credential_error" in text


def test_cleanup_page_gates_permanent_delete_behind_advanced_flag() -> None:
    text = (WEBUI / "js" / "views" / "cleanup.js").read_text(encoding="utf-8")

    assert 'advanced_enabled: advanced.checked' in text
    assert 'advanced.checked && permanentDelete.checked ? "permanent_delete"' in text
    assert '{ value: "permanent_delete"' not in text
