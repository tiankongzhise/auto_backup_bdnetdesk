from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import IO, Any


def native_path(path: str | Path) -> str:
    """Return a filesystem path usable with Windows long-path APIs."""
    actual = Path(path)
    if os.name != "nt":
        return str(actual)
    resolved = str(actual.resolve())
    if resolved.startswith("\\\\?\\"):
        return resolved
    if resolved.startswith("\\\\"):
        return "\\\\?\\UNC\\" + resolved[2:]
    return "\\\\?\\" + resolved


def open_file(path: str | Path, mode: str = "r", *args: Any, **kwargs: Any) -> IO[Any]:
    return open(native_path(path), mode, *args, **kwargs)


def exists(path: str | Path) -> bool:
    return os.path.exists(native_path(path))


def is_file(path: str | Path) -> bool:
    return os.path.isfile(native_path(path))


def stat(path: str | Path) -> os.stat_result:
    return os.stat(native_path(path))


def file_size(path: str | Path) -> int:
    return int(stat(path).st_size)


def mtime_seconds(path: str | Path) -> int:
    return int(stat(path).st_mtime)


def make_dirs(path: str | Path, *, exist_ok: bool = True) -> None:
    os.makedirs(native_path(path), exist_ok=exist_ok)


def unlink(path: str | Path, *, missing_ok: bool = False) -> None:
    try:
        os.unlink(native_path(path))
    except FileNotFoundError:
        if not missing_ok:
            raise


def remove_tree(path: str | Path) -> None:
    if exists(path):
        shutil.rmtree(native_path(path))


def replace_file(source: str | Path, target: str | Path) -> None:
    make_dirs(Path(target).parent)
    os.replace(native_path(source), native_path(target))
