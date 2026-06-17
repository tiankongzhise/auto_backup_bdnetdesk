from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from auto_backup_client.release_build import APP_NAME
from auto_backup_client.settings import ClientSettings


WEBVIEW2_CLIENT_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
RESOURCE_ROOTS = ("", "_internal")
REQUIRED_WEBUI_FILES = (
    "webui/index.html",
    "webui/styles.css",
    "webui/js/api.js",
    "webui/js/app.js",
    "webui/js/render.js",
    "webui/js/state.js",
    "webui/js/views/settings.js",
)
REQUIRED_MIGRATION_FILES = (
    "migrations/sqlite/001_sync_outbox.sql",
    "migrations/sqlite/012_restore_history_sync_fields.sql",
)
BLOCKED_RELEASE_FILENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "backup_state.sqlite3",
    "device_credentials.json",
}
BLOCKED_RELEASE_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".log"}
BLOCKED_RELEASE_DIR_NAMES = {"credentials", "logs"}
BLOCKED_RELEASE_DIR_TAILS = {
    ("var", "data"),
    ("var", "cache"),
}


@dataclass(frozen=True)
class PreflightFinding:
    check: str
    source: str
    detail: str

    def safe_line(self) -> str:
        return f"{self.check}: {self.source} ({self.detail})"


@dataclass(frozen=True)
class WebView2RuntimeStatus:
    checked: bool
    present: bool
    skipped: bool
    version: str = ""
    source: str = ""
    detail: str = ""

    @property
    def label(self) -> str:
        if self.skipped:
            return "skipped"
        return "present" if self.present else "missing"


@dataclass(frozen=True)
class PreflightResult:
    release_dir: str
    data_dir: str
    cache_dir: str
    checked_files: int
    webview2: WebView2RuntimeStatus
    findings: tuple[PreflightFinding, ...]

    @property
    def passed(self) -> bool:
        return not self.findings


def preflight_release_candidate(
    *,
    release_dir: Path,
    data_dir: Path | None = None,
    cache_dir: Path | None = None,
    check_webview2: bool = False,
    webview2_detector: Callable[[], WebView2RuntimeStatus] | None = None,
) -> PreflightResult:
    settings = ClientSettings.from_env()
    resolved_data_dir = data_dir or Path(settings.local_data_dir)
    resolved_cache_dir = cache_dir or Path(settings.local_cache_dir)
    findings: list[PreflightFinding] = []
    checked_files = 0

    if not release_dir.exists():
        findings.append(PreflightFinding("release_dir_exists", _safe_path(release_dir), "release directory is missing"))
    elif not release_dir.is_dir():
        findings.append(PreflightFinding("release_dir_type", _safe_path(release_dir), "release path must be a directory"))
    else:
        checked_files = sum(1 for path in release_dir.rglob("*") if path.is_file())
        findings.extend(_check_required_release_files(release_dir))
        findings.extend(_check_blocked_runtime_artifacts(release_dir))
        findings.extend(_check_user_data_boundary(release_dir, resolved_data_dir, "data_dir"))
        findings.extend(_check_user_data_boundary(release_dir, resolved_cache_dir, "cache_dir"))

    detector = webview2_detector or detect_webview2_runtime
    webview2 = detector() if check_webview2 else WebView2RuntimeStatus(
        checked=False,
        present=False,
        skipped=True,
        detail="not requested",
    )
    if check_webview2 and webview2.checked and not webview2.skipped and not webview2.present:
        findings.append(PreflightFinding("webview2_runtime", "WebView2", webview2.detail or "runtime is not installed"))

    return PreflightResult(
        release_dir=_safe_path(release_dir),
        data_dir=_safe_path(resolved_data_dir),
        cache_dir=_safe_path(resolved_cache_dir),
        checked_files=checked_files,
        webview2=webview2,
        findings=tuple(findings),
    )


def detect_webview2_runtime() -> WebView2RuntimeStatus:
    if os.name != "nt":
        return WebView2RuntimeStatus(
            checked=False,
            present=False,
            skipped=True,
            detail="WebView2 registry check is Windows-only",
        )
    try:
        import winreg
    except Exception as exc:
        return WebView2RuntimeStatus(checked=True, present=False, skipped=False, detail=f"winreg unavailable: {type(exc).__name__}")

    locations = (
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_GUID}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_GUID}"),
        (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_GUID}"),
    )
    for hive, subkey in locations:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                value, _value_type = winreg.QueryValueEx(key, "pv")
        except OSError:
            continue
        version = str(value or "").strip()
        if _webview2_version_is_valid(version):
            return WebView2RuntimeStatus(
                checked=True,
                present=True,
                skipped=False,
                version=version,
                source=_registry_source_name(winreg, hive, subkey),
            )
    return WebView2RuntimeStatus(
        checked=True,
        present=False,
        skipped=False,
        detail="WebView2 Runtime pv registry value was not found or was empty",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P3-14 R04/R11/R12 发布候选预检。")
    parser.add_argument("--release-dir", required=True, type=Path, help="PyInstaller onedir 发布目录。")
    parser.add_argument("--data-dir", type=Path, default=None, help="运行期用户数据目录；默认读取 LOCAL_DATA_DIR 或本机默认目录。")
    parser.add_argument("--cache-dir", type=Path, default=None, help="运行期缓存目录；默认读取 LOCAL_CACHE_DIR 或本机默认目录。")
    parser.add_argument("--check-webview2", action="store_true", help="在 Windows 上检查 Evergreen WebView2 Runtime 注册表 pv 值。")
    parsed = parser.parse_args(argv)

    result = preflight_release_candidate(
        release_dir=parsed.release_dir,
        data_dir=parsed.data_dir,
        cache_dir=parsed.cache_dir,
        check_webview2=parsed.check_webview2,
    )

    print(f"release_dir: {result.release_dir}", flush=True)
    print(f"data_dir: {result.data_dir}", flush=True)
    print(f"cache_dir: {result.cache_dir}", flush=True)
    print(f"checked_files: {result.checked_files}", flush=True)
    print(f"webview2_status: {result.webview2.label}", flush=True)
    if result.webview2.version:
        print(f"webview2_version: {result.webview2.version}", flush=True)
    print(f"finding_count: {len(result.findings)}", flush=True)
    for index, finding in enumerate(result.findings, start=1):
        print(f"finding_{index}: {finding.safe_line()}", flush=True)
    print(f"release_candidate_preflight_passed: {str(result.passed).lower()}", flush=True)
    return 0 if result.passed else 1


def _check_required_release_files(release_dir: Path) -> list[PreflightFinding]:
    findings: list[PreflightFinding] = []
    exe_name = f"{APP_NAME}.exe"
    if not (release_dir / exe_name).is_file():
        findings.append(PreflightFinding("required_executable", exe_name, "missing Windows GUI executable"))
    for relative in REQUIRED_WEBUI_FILES:
        if not _resource_exists(release_dir, relative):
            findings.append(PreflightFinding("required_webui_asset", relative, "missing bundled webui asset"))
    for relative in REQUIRED_MIGRATION_FILES:
        if not _resource_exists(release_dir, relative):
            findings.append(PreflightFinding("required_sqlite_migration", relative, "missing bundled SQLite migration"))
    if not _migration_dir_has_sql(release_dir):
        findings.append(PreflightFinding("sqlite_migration_bundle", "migrations/sqlite", "no SQLite migration SQL files found"))
    return findings


def _check_blocked_runtime_artifacts(release_dir: Path) -> list[PreflightFinding]:
    findings: list[PreflightFinding] = []
    for path in sorted(release_dir.rglob("*")):
        name = path.name.lower()
        if path.is_dir():
            parts = tuple(part.lower() for part in path.parts)
            if name in BLOCKED_RELEASE_DIR_NAMES:
                findings.append(PreflightFinding("blocked_runtime_dir", _safe_relative(release_dir, path), name))
            if len(parts) >= 2 and parts[-2:] in BLOCKED_RELEASE_DIR_TAILS:
                findings.append(PreflightFinding("blocked_runtime_dir", _safe_relative(release_dir, path), "/".join(parts[-2:])))
            continue
        if name in BLOCKED_RELEASE_FILENAMES:
            findings.append(PreflightFinding("blocked_runtime_file", _safe_relative(release_dir, path), name))
        if path.suffix.lower() in BLOCKED_RELEASE_SUFFIXES:
            findings.append(PreflightFinding("blocked_runtime_file", _safe_relative(release_dir, path), path.suffix.lower()))
    return findings


def _check_user_data_boundary(release_dir: Path, target_dir: Path, check_name: str) -> list[PreflightFinding]:
    if _is_within(target_dir, release_dir):
        return [
            PreflightFinding(
                check_name,
                _safe_path(target_dir),
                "runtime user data must not live inside the release program directory",
            )
        ]
    return []


def _resource_exists(release_dir: Path, relative: str) -> bool:
    return any((release_dir / root / relative).is_file() for root in RESOURCE_ROOTS)


def _migration_dir_has_sql(release_dir: Path) -> bool:
    for root in RESOURCE_ROOTS:
        migration_dir = release_dir / root / "migrations" / "sqlite"
        if migration_dir.is_dir() and any(path.suffix.lower() == ".sql" for path in migration_dir.iterdir() if path.is_file()):
            return True
    return False


def _webview2_version_is_valid(version: str) -> bool:
    cleaned = version.strip()
    if not cleaned or cleaned == "0.0.0.0":
        return False
    return any(ch.isdigit() and ch != "0" for ch in cleaned)


def _registry_source_name(winreg_module: object, hive: int, subkey: str) -> str:
    if hive == getattr(winreg_module, "HKEY_LOCAL_MACHINE", None):
        hive_name = "HKLM"
    elif hive == getattr(winreg_module, "HKEY_CURRENT_USER", None):
        hive_name = "HKCU"
    else:
        hive_name = "registry"
    return f"{hive_name}\\{subkey}"


def _is_within(child: Path, parent: Path) -> bool:
    child_resolved = child.resolve(strict=False)
    parent_resolved = parent.resolve(strict=False)
    try:
        child_resolved.relative_to(parent_resolved)
        return True
    except ValueError:
        return False


def _safe_relative(root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return _safe_path(path)
    parts = relative.parts
    if len(parts) <= 3:
        return relative.as_posix()
    return ".../" + "/".join(parts[-3:])


def _safe_path(path: Path) -> str:
    parts = path.parts
    if len(parts) <= 3:
        return path.name or str(path)
    return ".../" + "/".join(parts[-3:])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
