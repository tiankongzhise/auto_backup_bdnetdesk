from __future__ import annotations

from unittest.mock import Mock, patch

from auto_backup_client.archive_packager import SevenZipRunner
from auto_backup_client.restore_flow import SevenZipRestoreRunner
from auto_backup_client import subprocess_utils


def test_hidden_subprocess_kwargs_empty_on_non_windows() -> None:
    with patch.object(subprocess_utils.os, "name", "posix"):
        assert subprocess_utils.hidden_subprocess_kwargs() == {}


def test_hidden_subprocess_kwargs_sets_no_window_on_windows() -> None:
    startupinfo = Mock()
    startupinfo.dwFlags = 0
    with (
        patch.object(subprocess_utils.os, "name", "nt"),
        patch.object(subprocess_utils.subprocess, "STARTUPINFO", return_value=startupinfo),
        patch.object(subprocess_utils.subprocess, "STARTF_USESHOWWINDOW", 1, create=True),
        patch.object(subprocess_utils.subprocess, "SW_HIDE", 0, create=True),
        patch.object(subprocess_utils.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True),
    ):
        kwargs = subprocess_utils.hidden_subprocess_kwargs()

    assert kwargs["creationflags"] == 0x08000000
    assert kwargs["startupinfo"] is startupinfo
    assert startupinfo.dwFlags & 1
    assert startupinfo.wShowWindow == 0


def test_seven_zip_archive_runner_passes_hidden_window_kwargs(tmp_path, monkeypatch) -> None:
    archive = tmp_path / "archive.7z"
    source = tmp_path / "payload"
    source.mkdir()
    captured = {}

    def fake_run(args, **kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        archive.write_bytes(b"archive")
        return Mock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("auto_backup_client.archive_packager.subprocess.run", fake_run)
    marker = Mock()
    monkeypatch.setattr("auto_backup_client.archive_packager.hidden_subprocess_kwargs", lambda: {"creationflags": 1, "startupinfo": marker})

    SevenZipRunner(executable="7z").create_archive(archive_path=archive, staging_dir=source, password="secret")

    assert captured["creationflags"] == 1
    assert captured["startupinfo"] is marker


def test_seven_zip_restore_runner_passes_hidden_window_kwargs(tmp_path, monkeypatch) -> None:
    archive = tmp_path / "archive.7z"
    archive.write_bytes(b"archive")
    captured = {}

    def fake_run(args, **kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return Mock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("auto_backup_client.restore_flow.subprocess.run", fake_run)
    marker = Mock()
    monkeypatch.setattr("auto_backup_client.restore_flow.hidden_subprocess_kwargs", lambda: {"creationflags": 1, "startupinfo": marker})

    SevenZipRestoreRunner(executable="7z").test_archive(archive_path=archive, password="secret")

    assert captured["creationflags"] == 1
    assert captured["startupinfo"] is marker
