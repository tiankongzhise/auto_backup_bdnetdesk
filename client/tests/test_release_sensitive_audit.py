from __future__ import annotations

import json
import sqlite3

from auto_backup_client.release_sensitive_audit import audit_release_artifacts, main


def test_release_sensitive_audit_passes_clean_release_text(tmp_path) -> None:
    dist = tmp_path / "dist" / "AutoBackupBDNetdisk"
    dist.mkdir(parents=True)
    (dist / "readme.txt").write_text(
        "AutoBackupBDNetdisk release\nDevice Token: 已加载\nremote_path_digest: abcdef12\n",
        encoding="utf-8",
    )

    result = audit_release_artifacts(scan_paths=(dist,))

    assert result.passed is True
    assert result.scanned_files == 1
    assert result.findings == ()


def test_release_sensitive_audit_detects_file_content_and_masks_output(tmp_path, capsys) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    secret_log = log_dir / "run.log"
    secret_log.write_text(
        "Authorization: Bearer bdn_secretDeviceTokenValue12345\n"
        "source=C:\\Users\\Alice\\Pictures\\private.jpg\n",
        encoding="utf-8",
    )

    assert main(["--scan-path", str(log_dir)]) == 1
    output = capsys.readouterr().out

    assert "finding_count:" in output
    assert "authorization_bearer" in output
    assert "windows_path" in output
    assert "bdn_secretDeviceTokenValue12345" not in output
    assert "Alice" not in output
    assert "private.jpg" not in output


def test_release_sensitive_audit_detects_blocked_release_artifacts(tmp_path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / ".env").write_text("CLOUD_API_DEVICE_TOKEN=bdn_secretDeviceTokenValue12345", encoding="utf-8")
    (dist / "backup_state.sqlite3").write_bytes(b"not a real sqlite database")
    (dist / "manifest.json").write_text('{"manifest_version": 1, "entries": []}', encoding="utf-8")

    result = audit_release_artifacts(scan_paths=(dist,))

    kinds = {finding.kind for finding in result.findings}
    assert "blocked_filename" in kinds
    assert "sqlite_artifact" in kinds
    assert "manifest_artifact" in kinds
    assert "device_token" in kinds
    assert "manifest_plaintext" in kinds


def test_release_sensitive_audit_detects_sync_outbox_sensitive_payload(tmp_path) -> None:
    db_path = tmp_path / "state.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE sync_outbox (
            event_id TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            last_error TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO sync_outbox (event_id, payload_json, last_error)
        VALUES (?, ?, ?)
        """,
        (
            "evt_secret_1234567890",
            json.dumps(
                {
                    "entity_id": "backup_sources:1",
                    "job_name": "clean",
                    "local_path": "C:\\Users\\Alice\\Desktop\\source",
                    "nested": {"refresh_token": "fake-refresh-token-secret"},
                }
            ),
            "failed at C:\\Users\\Alice\\Desktop\\source",
        ),
    )
    conn.commit()
    conn.close()

    result = audit_release_artifacts(sqlite_paths=(db_path,))

    kinds = [finding.kind for finding in result.findings]
    assert "sensitive_payload_field" in kinds
    assert "baidu_refresh_token" in kinds
    assert "windows_path" in kinds
    assert all("Alice" not in finding.safe_line() for finding in result.findings)


def test_release_sensitive_audit_cli_requires_target(capsys) -> None:
    try:
        main([])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected argparse to reject missing targets")

    err = capsys.readouterr().err
    assert "at least one --scan-path or --sqlite-path is required" in err
