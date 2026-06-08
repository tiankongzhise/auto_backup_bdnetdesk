from __future__ import annotations

from pathlib import Path

from auto_backup_client import backup_pipeline_cli
from auto_backup_client.sqlite_store import new_id


def test_backup_pipeline_cli_local_run_outputs_counts_without_paths_or_password(monkeypatch, capsys) -> None:
    work = _short_work_dir()
    source = work / "source secret.txt"
    source.write_text("payload", encoding="utf-8")
    sqlite_path = work / "state.sqlite3"
    cache_root = work / "cache secret"

    monkeypatch.setattr(backup_pipeline_cli, "_read_archive_password", lambda _env: "Test123456789")

    exit_code = backup_pipeline_cli.main(
        [
            "--sqlite-path",
            str(sqlite_path),
            "--cache-root",
            str(cache_root),
            "--source",
            str(source),
            "--job-name",
            "local pipeline",
            "--no-complete",
        ]
    )

    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Device Token 来源: not_required" in output
    assert "scan_files: 1" in output
    assert "archive_sha256:" in output
    assert "端到端编排完成" in output
    assert str(source) not in output
    assert source.name not in output
    assert str(sqlite_path) not in output
    assert str(cache_root) not in output
    assert "Test123456789" not in output


def test_backup_pipeline_cli_rejects_missing_source_without_leaking_paths(capsys) -> None:
    work = _short_work_dir()
    exit_code = backup_pipeline_cli.main(
        [
            "--sqlite-path",
            str(work / "state.sqlite3"),
            "--cache-root",
            str(work / "cache"),
            "--no-complete",
        ]
    )

    output = capsys.readouterr().out

    assert exit_code == 1
    assert "source is required when job_id is not provided" in output
    assert str(work) not in output


def _short_work_dir() -> Path:
    work = Path(__file__).resolve().parents[2] / ".cache" / "pt" / f"abp_{new_id('test')[-12:]}"
    work.mkdir(parents=True, exist_ok=True)
    return work
