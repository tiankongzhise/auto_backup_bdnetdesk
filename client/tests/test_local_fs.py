from __future__ import annotations

import os
from pathlib import Path

from auto_backup_client import local_fs


def test_local_fs_long_path_operations_keep_business_path_plain(tmp_path) -> None:
    long_dir = tmp_path
    for index in range(8):
        long_dir = long_dir / f"segment-{index:02d}-abcdefghijklmnopqrstuvwxyz"
    local_fs.make_dirs(long_dir)

    source = long_dir / "source.txt"
    target = long_dir / "target.txt"
    with local_fs.open_file(source, "wb") as handle:
        handle.write(b"hello-long-path")

    assert len(str(source)) > 260
    assert "\\\\?\\" not in str(source)
    assert local_fs.exists(source)
    assert local_fs.is_file(source)
    assert local_fs.file_size(source) == len(b"hello-long-path")
    assert local_fs.mtime_seconds(source) > 0

    local_fs.replace_file(source, target)
    assert not local_fs.exists(source)
    with local_fs.open_file(target, "rb") as handle:
        assert handle.read() == b"hello-long-path"

    local_fs.remove_tree(long_dir)
    assert not local_fs.exists(long_dir)


def test_native_path_adds_windows_prefix_without_changing_non_windows_paths(tmp_path) -> None:
    path = tmp_path / "plain.txt"
    actual = local_fs.native_path(path)
    if os.name == "nt":
        assert actual.startswith("\\\\?\\")
    else:
        assert actual == str(path)
