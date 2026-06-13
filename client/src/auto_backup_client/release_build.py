from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence

from auto_backup_client import __version__


APP_NAME = "AutoBackupBDNetdisk"
REPO_ROOT = Path(__file__).resolve().parents[3]
CLIENT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = CLIENT_ROOT / "src"
ENTRY_SCRIPT = SOURCE_ROOT / "auto_backup_client" / "app.py"
DEFAULT_DIST_DIR = REPO_ROOT / "dist" / "client"
DEFAULT_WORK_DIR = REPO_ROOT / ".cache" / "pyinstaller"
DEFAULT_SPEC_DIR = REPO_ROOT / ".cache" / "pyinstaller-spec"
DEFAULT_MIGRATIONS_DIR = CLIENT_ROOT / "migrations" / "sqlite"


@dataclass(frozen=True)
class PyInstallerBuildConfig:
    app_name: str = APP_NAME
    build_id: str = ""
    dist_dir: Path = DEFAULT_DIST_DIR
    work_dir: Path = DEFAULT_WORK_DIR
    spec_dir: Path = DEFAULT_SPEC_DIR
    migrations_dir: Path = DEFAULT_MIGRATIONS_DIR
    clean: bool = True
    noconfirm: bool = True
    windowed: bool = True


def build_pyinstaller_args(config: PyInstallerBuildConfig | None = None) -> list[str]:
    selected = config or PyInstallerBuildConfig()
    build_id = resolve_build_id(selected.build_id)
    dist_dir = selected.dist_dir / build_id
    work_dir = selected.work_dir / build_id
    spec_dir = selected.spec_dir / build_id
    data_mapping = f"{selected.migrations_dir}{os.pathsep}migrations/sqlite"
    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name",
        selected.app_name,
        "--onedir",
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(work_dir),
        "--specpath",
        str(spec_dir),
        "--paths",
        str(SOURCE_ROOT),
        "--add-data",
        data_mapping,
    ]
    if selected.clean:
        args.append("--clean")
    if selected.noconfirm:
        args.append("--noconfirm")
    if selected.windowed:
        args.append("--windowed")
    args.append(str(ENTRY_SCRIPT))
    return args


def run_pyinstaller(config: PyInstallerBuildConfig, *, dry_run: bool = False) -> int:
    resolved_config = PyInstallerBuildConfig(
        app_name=config.app_name,
        build_id=resolve_build_id(config.build_id),
        dist_dir=config.dist_dir,
        work_dir=config.work_dir,
        spec_dir=config.spec_dir,
        migrations_dir=config.migrations_dir,
        clean=config.clean,
        noconfirm=config.noconfirm,
        windowed=config.windowed,
    )
    args = build_pyinstaller_args(resolved_config)
    print(" ".join(_quote_arg(arg) for arg in args))
    if dry_run:
        return 0
    (resolved_config.dist_dir / resolved_config.build_id).mkdir(parents=True, exist_ok=True)
    (resolved_config.work_dir / resolved_config.build_id).mkdir(parents=True, exist_ok=True)
    (resolved_config.spec_dir / resolved_config.build_id).mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(args, cwd=REPO_ROOT, check=False)
    return completed.returncode


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Windows desktop client release package.")
    parser.add_argument("--build-id", default="")
    parser.add_argument("--dist-dir", type=Path, default=DEFAULT_DIST_DIR)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--spec-dir", type=Path, default=DEFAULT_SPEC_DIR)
    parser.add_argument("--migrations-dir", type=Path, default=DEFAULT_MIGRATIONS_DIR)
    parser.add_argument("--name", default=APP_NAME)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-clean", action="store_true")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parsed = parser.parse_args(argv)
    config = PyInstallerBuildConfig(
        app_name=parsed.name,
        build_id=parsed.build_id,
        dist_dir=parsed.dist_dir,
        work_dir=parsed.work_dir,
        spec_dir=parsed.spec_dir,
        migrations_dir=parsed.migrations_dir,
        clean=not parsed.no_clean,
    )
    return run_pyinstaller(config, dry_run=parsed.dry_run)


def resolve_build_id(build_id: str = "") -> str:
    if not build_id or build_id.isspace():
        return datetime.now().strftime("%Y%m%d-%H%M%S")
    if any(ch in build_id for ch in '\\/:*?"<>|'):
        raise ValueError(f"Invalid build id: {build_id!r}")
    return build_id


def _quote_arg(arg: str) -> str:
    if not arg or any(ch.isspace() for ch in arg):
        return '"' + arg.replace('"', '\\"') + '"'
    return arg


if __name__ == "__main__":
    raise SystemExit(main())
