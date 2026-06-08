from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
from dataclasses import dataclass
from pathlib import Path

from auto_backup_client.local_fs import native_path


@dataclass(frozen=True)
class FileIdentity:
    size_bytes: int
    mtime_ns: int
    volume_serial: str
    file_index: str


def read_file_identity(path: str | Path) -> FileIdentity:
    stat_result = os.stat(native_path(path))
    volume_serial = ""
    file_index = ""
    if os.name == "nt":
        volume_serial, file_index = _read_windows_file_identity(path)
    return FileIdentity(
        size_bytes=int(stat_result.st_size),
        mtime_ns=_mtime_ns(stat_result),
        volume_serial=volume_serial,
        file_index=file_index,
    )


def identity_matches(
    observed: FileIdentity,
    *,
    expected_size: int,
    expected_mtime_ns: int,
    expected_volume_serial: str,
    expected_file_index: str,
) -> bool:
    if observed.size_bytes != expected_size or observed.mtime_ns != expected_mtime_ns:
        return False
    if expected_volume_serial and observed.volume_serial and observed.volume_serial != expected_volume_serial:
        return False
    if expected_file_index and observed.file_index and observed.file_index != expected_file_index:
        return False
    return True


def _mtime_ns(stat_result: os.stat_result) -> int:
    return int(getattr(stat_result, "st_mtime_ns", int(stat_result.st_mtime * 1_000_000_000)))


def _read_windows_file_identity(path: str | Path) -> tuple[str, str]:
    class FILETIME(ctypes.Structure):
        _fields_ = [
            ("dwLowDateTime", ctypes.wintypes.DWORD),
            ("dwHighDateTime", ctypes.wintypes.DWORD),
        ]

    class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", ctypes.wintypes.DWORD),
            ("ftCreationTime", FILETIME),
            ("ftLastAccessTime", FILETIME),
            ("ftLastWriteTime", FILETIME),
            ("dwVolumeSerialNumber", ctypes.wintypes.DWORD),
            ("nFileSizeHigh", ctypes.wintypes.DWORD),
            ("nFileSizeLow", ctypes.wintypes.DWORD),
            ("nNumberOfLinks", ctypes.wintypes.DWORD),
            ("nFileIndexHigh", ctypes.wintypes.DWORD),
            ("nFileIndexLow", ctypes.wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.wintypes.LPCWSTR,
        ctypes.wintypes.DWORD,
        ctypes.wintypes.DWORD,
        ctypes.wintypes.LPVOID,
        ctypes.wintypes.DWORD,
        ctypes.wintypes.DWORD,
        ctypes.wintypes.HANDLE,
    ]
    create_file.restype = ctypes.wintypes.HANDLE
    get_file_info = kernel32.GetFileInformationByHandle
    get_file_info.argtypes = [ctypes.wintypes.HANDLE, ctypes.POINTER(BY_HANDLE_FILE_INFORMATION)]
    get_file_info.restype = ctypes.wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.wintypes.HANDLE]
    close_handle.restype = ctypes.wintypes.BOOL

    GENERIC_READ = 0x80000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    FILE_SHARE_DELETE = 0x00000004
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x00000080
    INVALID_HANDLE_VALUE = ctypes.wintypes.HANDLE(-1).value

    handle = create_file(
        native_path(path),
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        None,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        None,
    )
    if handle == INVALID_HANDLE_VALUE:
        raise OSError(ctypes.get_last_error(), "CreateFileW failed", str(path))
    try:
        info = BY_HANDLE_FILE_INFORMATION()
        if not get_file_info(handle, ctypes.byref(info)):
            raise OSError(ctypes.get_last_error(), "GetFileInformationByHandle failed", str(path))
        volume_serial = f"{int(info.dwVolumeSerialNumber):08x}"
        file_index_value = (int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow)
        return volume_serial, f"{file_index_value:016x}"
    finally:
        close_handle(handle)
