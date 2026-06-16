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
