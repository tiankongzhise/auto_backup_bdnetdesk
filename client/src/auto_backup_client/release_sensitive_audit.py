from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SENSITIVE_OUTBOX_FIELDS = {
    "archive_path",
    "authorization",
    "authorization_password",
    "device_token",
    "encrypted_token_json",
    "error_message",
    "final_path",
    "local_archive_path",
    "local_cache_dir",
    "local_path",
    "manifest_path",
    "original_path",
    "password",
    "private_key",
    "quarantine_path",
    "refresh_token",
    "target_path",
    "uploadid",
    "wrapping_key",
    "wrapping_key_base64",
}

DEFAULT_TEXT_SUFFIXES = {
    ".cfg",
    ".conf",
    ".css",
    ".csv",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".log",
    ".md",
    ".ps1",
    ".sqlite-dump",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}

BLOCKED_FILENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "backup_state.sqlite3",
    "device_credentials.json",
    "manifest.json",
}

CONTENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("authorization_bearer", re.compile(r"\bAuthorization\s*:\s*Bearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE)),
    ("device_token", re.compile(r"\bbdn_[A-Za-z0-9._~+/=-]{16,}\b")),
    ("baidu_access_token", re.compile(r"[\"']?\baccess_token\b[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE)),
    ("baidu_refresh_token", re.compile(r"[\"']?\brefresh_token\b[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE)),
    ("password_value", re.compile(r"[\"']?\b(?:authorization_password|archive_password|password)\b[\"']?\s*[:=]\s*[\"']?[^\"'\s,;]{6,}", re.IGNORECASE)),
    ("wrapping_key", re.compile(r"[\"']?\b(?:wrapping_key|wrapping_key_base64|wrapped_key)\b[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE)),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("manifest_plaintext", re.compile(r"\"manifest_version\"|\"entries\"\s*:\s*\[|\"archive_members\"\s*:", re.IGNORECASE)),
    ("windows_path", re.compile(r"\b[A-Za-z]:\\(?:Users|tmp|Temp|Windows|ProgramData|Program Files|[^\\/:*?\"<>|\r\n]+\\)[^\"'\r\n]{2,}")),
)


@dataclass(frozen=True)
class Finding:
    source: str
    kind: str
    detail: str

    def safe_line(self) -> str:
        return f"{self.kind}: {self.source} ({self.detail})"


@dataclass(frozen=True)
class AuditResult:
    scanned_files: int
    scanned_sqlite: int
    findings: tuple[Finding, ...]

    @property
    def passed(self) -> bool:
        return not self.findings


def audit_release_artifacts(
    *,
    scan_paths: Sequence[Path] = (),
    sqlite_paths: Sequence[Path] = (),
    max_text_bytes: int = 2 * 1024 * 1024,
) -> AuditResult:
    findings: list[Finding] = []
    scanned_files = 0
    scanned_sqlite = 0

    for scan_path in scan_paths:
        if not scan_path.exists():
            findings.append(Finding(source=_safe_path(scan_path), kind="missing_scan_path", detail="path does not exist"))
            continue
        for file_path in _iter_files(scan_path):
            scanned_files += 1
            findings.extend(_inspect_filename(file_path))
            if _is_text_candidate(file_path):
                findings.extend(_inspect_text_file(file_path, max_text_bytes=max_text_bytes))

    for sqlite_path in sqlite_paths:
        scanned_sqlite += 1
        findings.extend(_inspect_sqlite_outbox(sqlite_path))

    return AuditResult(scanned_files=scanned_files, scanned_sqlite=scanned_sqlite, findings=tuple(findings))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P3-14 R14 发布候选敏感信息审计。")
    parser.add_argument("--scan-path", action="append", default=[], type=Path, help="要扫描的发布目录、日志目录或 UI 输出文件，可重复。")
    parser.add_argument("--sqlite-path", action="append", default=[], type=Path, help="要检查 sync_outbox 的 SQLite 数据库，可重复。")
    parser.add_argument("--max-text-bytes", type=int, default=2 * 1024 * 1024)
    parsed = parser.parse_args(argv)

    if not parsed.scan_path and not parsed.sqlite_path:
        parser.error("at least one --scan-path or --sqlite-path is required")
    if parsed.max_text_bytes < 1024:
        parser.error("--max-text-bytes must be at least 1024")

    result = audit_release_artifacts(
        scan_paths=tuple(parsed.scan_path),
        sqlite_paths=tuple(parsed.sqlite_path),
        max_text_bytes=parsed.max_text_bytes,
    )

    print(f"scanned_files: {result.scanned_files}", flush=True)
    print(f"scanned_sqlite: {result.scanned_sqlite}", flush=True)
    print(f"finding_count: {len(result.findings)}", flush=True)
    for index, finding in enumerate(result.findings, start=1):
        print(f"finding_{index}: {finding.safe_line()}", flush=True)
    print(f"release_sensitive_audit_passed: {str(result.passed).lower()}", flush=True)
    return 0 if result.passed else 1


def _iter_files(path: Path) -> Iterable[Path]:
    if path.is_file():
        yield path
        return
    for child in sorted(path.rglob("*")):
        if child.is_file():
            yield child


def _inspect_filename(path: Path) -> list[Finding]:
    name = path.name.lower()
    suffix = path.suffix.lower()
    findings: list[Finding] = []
    if name in BLOCKED_FILENAMES:
        findings.append(Finding(source=_safe_path(path), kind="blocked_filename", detail=name))
    if suffix in {".sqlite", ".sqlite3", ".db"}:
        findings.append(Finding(source=_safe_path(path), kind="sqlite_artifact", detail=suffix))
    if "manifest" in name and suffix in {".json", ".txt", ".log"}:
        findings.append(Finding(source=_safe_path(path), kind="manifest_artifact", detail=name))
    return findings


def _inspect_text_file(path: Path, *, max_text_bytes: int) -> list[Finding]:
    try:
        data = path.read_bytes()
    except OSError:
        return [Finding(source=_safe_path(path), kind="read_error", detail="cannot read file")]
    if len(data) > max_text_bytes:
        return []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            return []
    return _inspect_text(text, source=_safe_path(path))


def _inspect_text(text: str, *, source: str) -> list[Finding]:
    findings: list[Finding] = []
    for kind, pattern in CONTENT_PATTERNS:
        if pattern.search(text):
            findings.append(Finding(source=source, kind=kind, detail="matched redaction rule"))
    return findings


def _inspect_sqlite_outbox(path: Path) -> list[Finding]:
    if not path.exists():
        return [Finding(source=_safe_path(path), kind="missing_sqlite", detail="path does not exist")]
    findings: list[Finding] = []
    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return findings + [Finding(source=_safe_path(path), kind="sqlite_open_error", detail="cannot open database")]
    try:
        rows = conn.execute("SELECT event_id, payload_json, last_error FROM sync_outbox").fetchall()
    except sqlite3.Error:
        return findings + [Finding(source=_safe_path(path), kind="sqlite_schema_error", detail="sync_outbox unavailable")]
    finally:
        conn.close()

    for row in rows:
        event_id = _short_id(str(row["event_id"]))
        last_error = str(row["last_error"] or "")
        if last_error:
            for finding in _inspect_text(last_error, source=f"{_safe_path(path)}#sync_outbox:{event_id}:last_error"):
                findings.append(finding)
        payload_raw = str(row["payload_json"] or "")
        try:
            payload = json.loads(payload_raw)
        except ValueError:
            findings.append(Finding(source=f"{_safe_path(path)}#sync_outbox:{event_id}", kind="invalid_payload_json", detail="payload_json is not JSON"))
            continue
        if not isinstance(payload, Mapping):
            findings.append(Finding(source=f"{_safe_path(path)}#sync_outbox:{event_id}", kind="invalid_payload_json", detail="payload_json is not object"))
            continue
        findings.extend(_inspect_payload(payload, source=f"{_safe_path(path)}#sync_outbox:{event_id}"))
        for finding in _inspect_text(payload_raw, source=f"{_safe_path(path)}#sync_outbox:{event_id}:payload_json"):
            findings.append(finding)
    return findings


def _inspect_payload(value: Any, *, source: str, path: str = "$") -> list[Finding]:
    findings: list[Finding] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            text_key = str(key)
            child_path = f"{path}.{text_key}"
            if _is_sensitive_field(text_key):
                findings.append(Finding(source=source, kind="sensitive_payload_field", detail=child_path))
            findings.extend(_inspect_payload(item, source=source, path=child_path))
        return findings
    if isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(_inspect_payload(item, source=source, path=f"{path}[{index}]"))
        return findings
    if isinstance(value, str) and value:
        for finding in _inspect_text(value, source=f"{source}:{path}"):
            findings.append(finding)
    return findings


def _is_sensitive_field(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return (
        normalized in SENSITIVE_OUTBOX_FIELDS
        or normalized.endswith("_token")
        or normalized.endswith("_secret")
        or normalized.endswith("_password")
    )


def _is_text_candidate(path: Path) -> bool:
    name = path.name.lower()
    if name in BLOCKED_FILENAMES:
        return True
    return path.suffix.lower() in DEFAULT_TEXT_SUFFIXES


def _safe_path(path: Path) -> str:
    parts = path.parts
    if len(parts) <= 3:
        return path.name or str(path)
    return ".../" + "/".join(parts[-3:])


def _short_id(value: str) -> str:
    return value[:12] if value else "unknown"


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
