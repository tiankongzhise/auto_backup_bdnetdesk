from __future__ import annotations

import sys
from pathlib import Path

from auto_backup_client.settings import ClientSettings
from auto_backup_client.sqlite_store import SQLiteClientStore
from auto_backup_client.webview_bridge import AutoBackupWebviewBridge


def webui_index_path() -> Path:
    bundled_root = getattr(sys, "_MEIPASS", None)
    if bundled_root:
        return Path(bundled_root) / "webui" / "index.html"
    return Path(__file__).resolve().parent / "webui" / "index.html"


def run_webview_app(settings: ClientSettings | None = None) -> int:
    runtime_settings = settings or ClientSettings.from_env()
    Path(runtime_settings.local_data_dir).mkdir(parents=True, exist_ok=True)
    Path(runtime_settings.local_cache_dir).mkdir(parents=True, exist_ok=True)
    store = SQLiteClientStore(runtime_settings.local_sqlite_path)
    store.migrate()

    bridge = AutoBackupWebviewBridge(settings=runtime_settings, store=store)
    index_path = webui_index_path()
    if not index_path.exists():
        raise FileNotFoundError(f"未找到 pywebview 静态入口：{index_path}")

    import webview  # type: ignore[import-not-found]

    window = webview.create_window(
        "Auto Backup BD Netdisk",
        str(index_path),
        js_api=bridge,
        width=1280,
        height=820,
        min_size=(1100, 720),
    )
    bridge.set_window(window)
    webview.start(debug=False)
    return 0


__all__ = ["run_webview_app", "webui_index_path"]
