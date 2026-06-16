from __future__ import annotations

import os
from pathlib import Path

from auto_backup_client.release_build import (
    APP_NAME,
    ENTRY_SCRIPT,
    SOURCE_ROOT,
    DEFAULT_WEBUI_DIR,
    PyInstallerBuildConfig,
    build_pyinstaller_args,
    resolve_build_id,
)


def test_pyinstaller_args_build_windowed_onedir_package(tmp_path) -> None:
    config = PyInstallerBuildConfig(
        build_id="test-build-plan",
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
    assert _option_value(args, "--distpath") == str(tmp_path / "dist" / "test-build-plan")
    assert _option_value(args, "--workpath") == str(tmp_path / "work" / "test-build-plan")
    assert _option_value(args, "--specpath") == str(tmp_path / "spec" / "test-build-plan")
    assert _option_value(args, "--paths") == str(SOURCE_ROOT)
    assert args[-1] == str(ENTRY_SCRIPT)


def test_pyinstaller_args_include_sqlite_migrations_data(tmp_path) -> None:
    migrations_dir = tmp_path / "client" / "migrations" / "sqlite"
    args = build_pyinstaller_args(PyInstallerBuildConfig(migrations_dir=migrations_dir))

    data_args = _option_values(args, "--add-data")

    assert f"{migrations_dir}{os.pathsep}migrations/sqlite" in data_args


def test_pyinstaller_args_include_webui_static_assets() -> None:
    args = build_pyinstaller_args(PyInstallerBuildConfig(build_id="test-webui-assets"))

    data_args = _option_values(args, "--add-data")

    assert f"{DEFAULT_WEBUI_DIR}{os.pathsep}webui" in data_args


def test_pyinstaller_args_append_build_id_to_default_dirs() -> None:
    args = build_pyinstaller_args(PyInstallerBuildConfig(build_id="20260613-120000"))

    assert _option_value(args, "--distpath").endswith(
        str(Path("dist") / "client" / "20260613-120000")
    )
    assert _option_value(args, "--workpath").endswith(
        str(Path(".cache") / "pyinstaller" / "20260613-120000")
    )
    assert _option_value(args, "--specpath").endswith(
        str(Path(".cache") / "pyinstaller-spec" / "20260613-120000")
    )


def test_resolve_build_id_rejects_windows_unsafe_folder_name() -> None:
    try:
        resolve_build_id("bad:id")
    except ValueError as exc:
        assert "Invalid build id" in str(exc)
    else:
        raise AssertionError("expected invalid build id to be rejected")


def _option_value(args: list[str], option: str) -> str:
    index = args.index(option)
    return args[index + 1]


def _option_values(args: list[str], option: str) -> list[str]:
    return [args[index + 1] for index, value in enumerate(args[:-1]) if value == option]
