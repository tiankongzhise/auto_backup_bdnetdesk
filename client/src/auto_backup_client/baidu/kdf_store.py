from __future__ import annotations

import base64
import ctypes
import json
import os
import tempfile
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from auto_backup_client.baidu.crypto import BAIDU_ENCRYPTION_PASSWORD, Argon2idParams, derive_password_wrapping_key


STORE_VERSION = 1
PAYLOAD_VERSION = 1
PROTECTION_DPAPI = "windows_dpapi_current_user_v1"
PROTECTION_PLAINTEXT = "plaintext_test_only_v1"
KDF_NAME = "argon2id"
DEFAULT_STORE_ENV = "AUTO_BACKUP_BAIDU_KDF_STORE_PATH"
ALLOW_PLAINTEXT_ENV = "AUTO_BACKUP_BAIDU_KDF_STORE_ALLOW_PLAINTEXT"


class PasswordKDFStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class PasswordKDFRecord:
    account_id: str
    salt: bytes
    time_cost: int
    memory_cost_kib: int
    parallelism: int
    hash_len: int = 32
    encryption_method: str = BAIDU_ENCRYPTION_PASSWORD
    kdf: str = KDF_NAME
    token_version: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_params(
        cls,
        *,
        account_id: str,
        params: Argon2idParams,
        token_version: int = 0,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> "PasswordKDFRecord":
        return cls(
            account_id=_clean_account_id(account_id),
            salt=params.salt,
            time_cost=params.time_cost,
            memory_cost_kib=params.memory_cost_kib,
            parallelism=params.parallelism,
            hash_len=params.hash_len,
            token_version=max(0, int(token_version)),
            created_at=created_at,
            updated_at=updated_at,
        )

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "PasswordKDFRecord":
        account_id = _clean_account_id(str(data.get("account_id", "")))
        kdf = str(data.get("kdf", ""))
        encryption_method = str(data.get("encryption_method", ""))
        if kdf != KDF_NAME:
            raise PasswordKDFStoreError(f"unsupported password KDF: {kdf}")
        if encryption_method != BAIDU_ENCRYPTION_PASSWORD:
            raise PasswordKDFStoreError(f"unsupported encryption method: {encryption_method}")
        salt = _b64url_decode(str(data.get("salt", "")))
        params = Argon2idParams(
            salt=salt,
            time_cost=int(data.get("time_cost", 0)),
            memory_cost_kib=int(data.get("memory_cost_kib", 0)),
            parallelism=int(data.get("parallelism", 0)),
            hash_len=int(data.get("hash_len", 0)),
        )
        _validate_params(params)
        return cls(
            account_id=account_id,
            salt=params.salt,
            time_cost=params.time_cost,
            memory_cost_kib=params.memory_cost_kib,
            parallelism=params.parallelism,
            hash_len=params.hash_len,
            token_version=int(data.get("token_version", 0)),
            encryption_method=encryption_method,
            kdf=kdf,
            created_at=_parse_datetime(data.get("created_at")),
            updated_at=_parse_datetime(data.get("updated_at")),
        )

    def to_json(self) -> dict[str, Any]:
        _validate_params(self.to_params())
        return {
            "account_id": self.account_id,
            "encryption_method": self.encryption_method,
            "kdf": self.kdf,
            "salt": _b64url_encode(self.salt),
            "time_cost": self.time_cost,
            "memory_cost_kib": self.memory_cost_kib,
            "parallelism": self.parallelism,
            "hash_len": self.hash_len,
            "token_version": self.token_version,
            "created_at": _format_datetime(self.created_at),
            "updated_at": _format_datetime(self.updated_at),
        }

    def to_params(self) -> Argon2idParams:
        return Argon2idParams(
            salt=self.salt,
            time_cost=self.time_cost,
            memory_cost_kib=self.memory_cost_kib,
            parallelism=self.parallelism,
            hash_len=self.hash_len,
        )

    def derive_wrapping_key(self, password: str) -> bytes:
        return derive_password_wrapping_key(password, self.to_params())


class PasswordKDFStore:
    def __init__(self, path: str | Path | None = None, *, allow_plaintext: bool = False) -> None:
        self.path = Path(path) if path is not None else default_kdf_store_path()
        self.allow_plaintext = allow_plaintext

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "PasswordKDFStore":
        source = os.environ if env is None else env
        path = source.get(DEFAULT_STORE_ENV, "").strip() or None
        allow_plaintext = _truthy(source.get(ALLOW_PLAINTEXT_ENV, ""))
        return cls(path, allow_plaintext=allow_plaintext)

    def get_record(self, account_id: str) -> PasswordKDFRecord | None:
        records = self._load_records()
        return records.get(_clean_account_id(account_id))

    def require_record(self, account_id: str) -> PasswordKDFRecord:
        record = self.get_record(account_id)
        if record is None:
            raise PasswordKDFStoreError(f"password KDF material is not saved for account {account_id}")
        return record

    def derive_wrapping_key(self, account_id: str, password: str) -> bytes:
        return self.require_record(account_id).derive_wrapping_key(password)

    def save_record(self, record: PasswordKDFRecord) -> PasswordKDFRecord:
        records = self._load_records()
        now = datetime.now(timezone.utc)
        existing = records.get(record.account_id)
        saved = PasswordKDFRecord(
            account_id=record.account_id,
            salt=record.salt,
            time_cost=record.time_cost,
            memory_cost_kib=record.memory_cost_kib,
            parallelism=record.parallelism,
            hash_len=record.hash_len,
            encryption_method=record.encryption_method,
            kdf=record.kdf,
            token_version=record.token_version,
            created_at=record.created_at or (existing.created_at if existing else now),
            updated_at=now,
        )
        records[saved.account_id] = saved
        self._save_records(records)
        return saved

    def save_material(
        self,
        *,
        account_id: str,
        salt: bytes,
        time_cost: int,
        memory_cost_kib: int,
        parallelism: int,
        hash_len: int,
        token_version: int = 0,
    ) -> PasswordKDFRecord:
        return self.save_record(
            PasswordKDFRecord(
                account_id=_clean_account_id(account_id),
                salt=salt,
                time_cost=time_cost,
                memory_cost_kib=memory_cost_kib,
                parallelism=parallelism,
                hash_len=hash_len,
                token_version=max(0, int(token_version)),
            )
        )

    def _load_records(self) -> dict[str, PasswordKDFRecord]:
        if not self.path.exists():
            return {}
        try:
            wrapper = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(wrapper, dict):
                raise PasswordKDFStoreError("password KDF store wrapper must be a JSON object")
            payload = self._decode_payload(wrapper)
            records = payload.get("records", {})
            if not isinstance(records, dict):
                raise PasswordKDFStoreError("password KDF store records must be a JSON object")
            return {account_id: PasswordKDFRecord.from_json(record) for account_id, record in records.items()}
        except PasswordKDFStoreError:
            raise
        except Exception as exc:
            raise PasswordKDFStoreError("failed to read password KDF store") from exc

    def _save_records(self, records: Mapping[str, PasswordKDFRecord]) -> None:
        payload = {
            "version": PAYLOAD_VERSION,
            "records": {account_id: record.to_json() for account_id, record in sorted(records.items())},
        }
        wrapper = self._encode_payload(payload)
        data = json.dumps(wrapper, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def _encode_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if os.name == "nt" and not self.allow_plaintext:
            return {
                "version": STORE_VERSION,
                "protection": PROTECTION_DPAPI,
                "ciphertext": _b64url_encode(_dpapi_protect(raw)),
            }
        if not self.allow_plaintext:
            raise PasswordKDFStoreError("plaintext password KDF store requires explicit allow_plaintext=True")
        return {
            "version": STORE_VERSION,
            "protection": PROTECTION_PLAINTEXT,
            "payload": payload,
        }

    def _decode_payload(self, wrapper: Mapping[str, Any]) -> dict[str, Any]:
        if int(wrapper.get("version", 0)) != STORE_VERSION:
            raise PasswordKDFStoreError("unsupported password KDF store version")
        protection = str(wrapper.get("protection", ""))
        if protection == PROTECTION_DPAPI:
            raw = _dpapi_unprotect(_b64url_decode(str(wrapper.get("ciphertext", ""))))
            payload = json.loads(raw.decode("utf-8"))
        elif protection == PROTECTION_PLAINTEXT:
            if not self.allow_plaintext:
                raise PasswordKDFStoreError("plaintext password KDF store is not allowed")
            payload = wrapper.get("payload")
        else:
            raise PasswordKDFStoreError(f"unsupported password KDF store protection: {protection}")
        if not isinstance(payload, dict):
            raise PasswordKDFStoreError("password KDF store payload must be a JSON object")
        if int(payload.get("version", 0)) != PAYLOAD_VERSION:
            raise PasswordKDFStoreError("unsupported password KDF payload version")
        return dict(payload)


def default_kdf_store_path() -> Path:
    configured = os.environ.get(DEFAULT_STORE_ENV, "").strip()
    if configured:
        return Path(configured)
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        root = Path(local_app_data)
    else:
        root = Path.home() / "AppData" / "Local"
    return root / "auto_backup_bdnetdesk" / "credentials" / "baidu_password_kdf_store.json"


def _validate_params(params: Argon2idParams) -> None:
    if len(params.salt) < 16:
        raise PasswordKDFStoreError("password KDF salt must be at least 16 bytes")
    if params.time_cost <= 0:
        raise PasswordKDFStoreError("password KDF time_cost must be positive")
    if params.memory_cost_kib <= 0:
        raise PasswordKDFStoreError("password KDF memory_cost_kib must be positive")
    if params.parallelism <= 0:
        raise PasswordKDFStoreError("password KDF parallelism must be positive")
    if params.hash_len != 32:
        raise PasswordKDFStoreError("password KDF hash_len must be 32")


def _clean_account_id(account_id: str) -> str:
    cleaned = account_id.strip()
    if not cleaned:
        raise PasswordKDFStoreError("account_id is required for password KDF material")
    return cleaned


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    if not value:
        raise PasswordKDFStoreError("base64url value is required")
    padded = value + ("=" * ((4 - len(value) % 4) % 4))
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except Exception as exc:
        raise PasswordKDFStoreError("invalid base64url value") from exc


def _format_datetime(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _dpapi_protect(data: bytes) -> bytes:
    if os.name != "nt":
        raise PasswordKDFStoreError("Windows DPAPI is only available on Windows")
    return _crypt_protect_data(data, protect=True)


def _dpapi_unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        raise PasswordKDFStoreError("Windows DPAPI is only available on Windows")
    return _crypt_protect_data(data, protect=False)


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _crypt_protect_data(data: bytes, *, protect: bool) -> bytes:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    input_buffer = ctypes.create_string_buffer(data)
    input_blob = _DataBlob(len(data), ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_byte)))
    output_blob = _DataBlob()
    entropy_bytes = b"auto_backup_bdnetdesk.baidu.password_kdf.v1"
    entropy_buffer = ctypes.create_string_buffer(entropy_bytes)
    entropy_blob = _DataBlob(len(entropy_bytes), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_byte)))
    flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN
    if protect:
        ok = crypt32.CryptProtectData(
            ctypes.byref(input_blob),
            None,
            ctypes.byref(entropy_blob),
            None,
            None,
            flags,
            ctypes.byref(output_blob),
        )
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(input_blob),
            None,
            ctypes.byref(entropy_blob),
            None,
            None,
            flags,
            ctypes.byref(output_blob),
        )
    if not ok:
        raise PasswordKDFStoreError(f"Windows DPAPI operation failed with error {ctypes.get_last_error()}")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)
