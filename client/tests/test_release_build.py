from __future__ import annotations

import os
from pathlib import Path

from auto_backup_client.release_build import (
    APP_NAME,
    ENTRY_SCRIPT,
    SOURCE_ROOT,
    PyInstallerBuildConfig,
    build_pyinstaller_args,
)


def test_pyinstaller_args_build_windowed_onedir_package(tmp_path) -> None:
    config = PyInstallerBuildConfig(
        dist_dir=tmp_path / "dist",
        work_dir=tmp_path / "work",
        spec_dir=tmp_path / "spec",
        migrations_dir=tmp_path / "migrations" / "sqlite",
    )

    args = build_pyinstaller_args(config)

    assert args[:3] == [os.sys.executable, "-m", "PyInstaller"]
    assert _option_value(args, "--name") == APP_NAME
    assert "--onedir" in args
    assert "--windowed" in args
    assert "--clean" in args
    assert "--noconfirm" in args
    assert _option_value(args, "--distpath") == str(tmp_path / "dist")
    assert _option_value(args, "--workpath") == str(tmp_path / "work")
    assert _option_value(args, "--specpath") == str(tmp_path / "spec")
    assert _option_value(args, "--paths") == str(SOURCE_ROOT)
    assert args[-1] == str(ENTRY_SCRIPT)


def test_pyinstaller_args_include_sqlite_migrations_data(tmp_path) -> None:
    migrations_dir = tmp_path / "client" / "migrations" / "sqlite"
    args = build_pyinstaller_args(PyInstallerBuildConfig(migrations_dir=migrations_dir))

    data_arg = _option_value(args, "--add-data")

    assert data_arg == f"{migrations_dir}{os.pathsep}migrations/sqlite"


def _option_value(args: list[str], option: str) -> str:
    index = args.index(option)
    return args[index + 1]
