from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import platform
import socket
import tempfile
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from auto_backup_client.baidu.cloud_api import BaiduCloudClient, DeviceRegistration, register_device


STORE_VERSION = 1
PAYLOAD_VERSION = 1
PROTECTION_DPAPI = "windows_dpapi_current_user_v1"
PROTECTION_PLAINTEXT = "plaintext_test_only_v1"
DEFAULT_STORE_ENV = "AUTO_BACKUP_DEVICE_CREDENTIAL_STORE_PATH"
ALLOW_PLAINTEXT_ENV = "AUTO_BACKUP_DEVICE_CREDENTIAL_STORE_ALLOW_PLAINTEXT"
DEVICE_ID_NAMESPACE = "auto_backup_bdnetdesk.device_id.v1"


class DeviceCredentialStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeviceIdentity:
    device_id: str
    fingerprint_hash: str
    feature_keys: tuple[str, ...] = tuple()


@dataclass(frozen=True)
class DeviceCredentials:
    cloud_api_base_url: str
    device_id: str
    device_token: str
    device_fingerprint_hash: str = ""
    device_name: str = ""
    hostname: str = ""
    os_version: str = ""
    client_version: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_registration(
        cls,
        registration: DeviceRegistration,
        *,
        cloud_api_base_url: str,
        device_name: str,
        hostname: str,
        os_version: str,
        client_version: str,
        device_fingerprint_hash: str = "",
    ) -> "DeviceCredentials":
        return cls(
            cloud_api_base_url=_clean_base_url(cloud_api_base_url),
            device_id=_clean_required(registration.device_id, "device_id"),
            device_token=_clean_required(registration.device_token, "device_token"),
            device_fingerprint_hash=device_fingerprint_hash.strip().lower(),
            device_name=device_name.strip(),
            hostname=hostname.strip(),
            os_version=os_version.strip(),
            client_version=client_version.strip(),
        )

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "DeviceCredentials":
        return cls(
            cloud_api_base_url=_clean_base_url(str(data.get("cloud_api_base_url", ""))),
            device_id=_clean_required(str(data.get("device_id", "")), "device_id"),
            device_token=_clean_required(str(data.get("device_token", "")), "device_token"),
            device_fingerprint_hash=str(data.get("device_fingerprint_hash", "")).strip().lower(),
            device_name=str(data.get("device_name", "")).strip(),
            hostname=str(data.get("hostname", "")).strip(),
            os_version=str(data.get("os_version", "")).strip(),
            client_version=str(data.get("client_version", "")).strip(),
            created_at=_parse_datetime(data.get("created_at")),
            updated_at=_parse_datetime(data.get("updated_at")),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "cloud_api_base_url": self.cloud_api_base_url,
            "device_id": self.device_id,
            "device_token": self.device_token,
            "device_fingerprint_hash": self.device_fingerprint_hash,
            "device_name": self.device_name,
            "hostname": self.hostname,
            "os_version": self.os_version,
            "client_version": self.client_version,
            "created_at": _format_datetime(self.created_at),
            "updated_at": _format_datetime(self.updated_at),
        }


class DeviceCredentialStore:
    def __init__(self, path: str | Path | None = None, *, allow_plaintext: bool = False) -> None:
        self.path = Path(path) if path is not None else default_device_credential_store_path()
        self.allow_plaintext = allow_plaintext

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "DeviceCredentialStore":
        source = os.environ if env is None else env
        path = source.get(DEFAULT_STORE_ENV, "").strip() or None
        allow_plaintext = _truthy(source.get(ALLOW_PLAINTEXT_ENV, ""))
        return cls(path, allow_plaintext=allow_plaintext)

    def load(self) -> DeviceCredentials | None:
        if not self.path.exists():
            return None
        try:
            wrapper = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(wrapper, dict):
                raise DeviceCredentialStoreError("device credential store wrapper must be a JSON object")
            payload = self._decode_payload(wrapper)
            credential = payload.get("credential")
            if credential is None:
                return None
            if not isinstance(credential, dict):
                raise DeviceCredentialStoreError("device credential payload must be a JSON object")
            return DeviceCredentials.from_json(credential)
        except DeviceCredentialStoreError:
            raise
        except Exception as exc:
            raise DeviceCredentialStoreError("failed to read device credential store") from exc

    def save(self, credentials: DeviceCredentials) -> DeviceCredentials:
        now = datetime.now(timezone.utc)
        existing = self.load()
        saved = DeviceCredentials(
            cloud_api_base_url=_clean_base_url(credentials.cloud_api_base_url),
            device_id=_clean_required(credentials.device_id, "device_id"),
            device_token=_clean_required(credentials.device_token, "device_token"),
            device_fingerprint_hash=credentials.device_fingerprint_hash,
            device_name=credentials.device_name,
            hostname=credentials.hostname,
            os_version=credentials.os_version,
            client_version=credentials.client_version,
            created_at=credentials.created_at or (existing.created_at if existing else now),
            updated_at=now,
        )
        self._save_payload({"version": PAYLOAD_VERSION, "credential": saved.to_json()})
        return saved

    def load_for_base_url(self, cloud_api_base_url: str) -> DeviceCredentials | None:
        credentials = self.load()
        if credentials is None:
            return None
        if _clean_base_url(credentials.cloud_api_base_url) != _clean_base_url(cloud_api_base_url):
            return None
        return credentials

    def _save_payload(self, payload: Mapping[str, Any]) -> None:
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
            raise DeviceCredentialStoreError("plaintext device credential store requires explicit allow_plaintext=True")
        return {
            "version": STORE_VERSION,
            "protection": PROTECTION_PLAINTEXT,
            "payload": payload,
        }

    def _decode_payload(self, wrapper: Mapping[str, Any]) -> dict[str, Any]:
        if int(wrapper.get("version", 0)) != STORE_VERSION:
            raise DeviceCredentialStoreError("unsupported device credential store version")
        protection = str(wrapper.get("protection", ""))
        if protection == PROTECTION_DPAPI:
            raw = _dpapi_unprotect(_b64url_decode(str(wrapper.get("ciphertext", ""))))
            payload = json.loads(raw.decode("utf-8"))
        elif protection == PROTECTION_PLAINTEXT:
            if not self.allow_plaintext:
                raise DeviceCredentialStoreError("plaintext device credential store is not allowed")
            payload = wrapper.get("payload")
        else:
            raise DeviceCredentialStoreError(f"unsupported device credential store protection: {protection}")
        if not isinstance(payload, dict):
            raise DeviceCredentialStoreError("device credential payload must be a JSON object")
        if int(payload.get("version", 0)) != PAYLOAD_VERSION:
            raise DeviceCredentialStoreError("unsupported device credential payload version")
        return dict(payload)


def resolve_or_register_device_credentials(
    *,
    cloud_api_base_url: str,
    provided_device_token: str = "",
    store: DeviceCredentialStore | None = None,
    client_version: str = "0.1.0",
) -> tuple[DeviceCredentials, str]:
    base_url = _clean_base_url(cloud_api_base_url)
    actual_store = store or DeviceCredentialStore.from_env()
    if provided_device_token.strip():
        token = provided_device_token.strip()
        try:
            saved = actual_store.load_for_base_url(base_url)
        except DeviceCredentialStoreError:
            saved = None
        if saved is not None and saved.device_token == token:
            return (
                DeviceCredentials(
                    cloud_api_base_url=base_url,
                    device_id=saved.device_id,
                    device_token=token,
                    device_fingerprint_hash=saved.device_fingerprint_hash,
                    device_name=saved.device_name,
                    hostname=saved.hostname,
                    os_version=saved.os_version,
                    client_version=client_version or saved.client_version,
                    created_at=saved.created_at,
                    updated_at=saved.updated_at,
                ),
                "运行环境 + 本机 DPAPI 凭据",
            )
        remote = _current_device_from_token(base_url, token)
        return (
            DeviceCredentials(
                cloud_api_base_url=base_url,
                device_id=_clean_required(remote.device_id, "device_id"),
                device_token=token,
                device_fingerprint_hash="",
                device_name=remote.device_name,
                hostname=remote.hostname,
                os_version=remote.os_version,
                client_version=client_version or remote.client_version,
            ),
            "运行环境 + 云端当前设备",
        )
    saved = actual_store.load_for_base_url(base_url)
    if saved is not None:
        return saved, "本机 DPAPI 凭据"

    hostname = socket.gethostname()
    os_version = platform.platform()
    device_name = f"auto-backup-{hostname}".strip("-")
    identity = derive_stable_device_identity(_collect_device_identity_features())
    registration = register_device(
        base_url,
        device_id=identity.device_id,
        device_fingerprint_hash=identity.fingerprint_hash,
        device_name=device_name,
        hostname=hostname,
        os_version=os_version,
        client_version=client_version,
    )
    credentials = actual_store.save(
        DeviceCredentials.from_registration(
            registration,
            cloud_api_base_url=base_url,
            device_name=device_name,
            hostname=hostname,
            os_version=os_version,
            client_version=client_version,
            device_fingerprint_hash=identity.fingerprint_hash,
        )
    )
    return credentials, "新注册并保存到本机 DPAPI"


def _current_device_from_token(cloud_api_base_url: str, device_token: str) -> Any:
    try:
        with BaiduCloudClient(cloud_api_base_url, device_token, timeout=10.0) as cloud:
            return cloud.current_device()
    except Exception as exc:
        raise DeviceCredentialStoreError("failed to resolve device_id from runtime Device Token") from exc


def derive_stable_device_identity(features: Mapping[str, str]) -> DeviceIdentity:
    cleaned = _clean_identity_features(features)
    if not cleaned:
        raise DeviceCredentialStoreError("stable device identity features are unavailable")
    canonical = json.dumps(
        {
            "namespace": DEVICE_ID_NAMESPACE,
            "features": [[key, cleaned[key]] for key in sorted(cleaned)],
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )
    fingerprint_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return DeviceIdentity(
        device_id=_device_id_from_fingerprint_hash(fingerprint_hash),
        fingerprint_hash=fingerprint_hash,
        feature_keys=tuple(sorted(cleaned)),
    )


def current_machine_device_identity() -> DeviceIdentity:
    return derive_stable_device_identity(_collect_device_identity_features())


def _collect_device_identity_features() -> dict[str, str]:
    features: dict[str, str] = {}
    if os.name == "nt":
        machine_guid = _windows_machine_guid()
        if machine_guid:
            features["windows_machine_guid"] = machine_guid
        return features

    machine_id = _read_first_text_file(("/etc/machine-id", "/var/lib/dbus/machine-id"))
    if machine_id:
        features["machine_id"] = machine_id
    return features


def _clean_identity_features(features: Mapping[str, str]) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for raw_key, raw_value in features.items():
        key = str(raw_key).strip().lower()
        value = str(raw_value).strip().lower()
        if key and value:
            cleaned[key] = value
    return cleaned


def _device_id_from_fingerprint_hash(fingerprint_hash: str) -> str:
    value = fingerprint_hash.strip().lower()
    if not _is_sha256_hex(value):
        raise DeviceCredentialStoreError("device fingerprint hash must be 64 lowercase hex characters")
    return f"dev_{value[0:8]}-{value[8:12]}-{value[12:16]}-{value[16:20]}-{value[20:32]}"


def _windows_machine_guid() -> str:
    if os.name != "nt":
        return ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
            value, _value_type = winreg.QueryValueEx(key, "MachineGuid")
    except Exception:
        return ""
    return str(value).strip()


def _read_first_text_file(paths: tuple[str, ...]) -> str:
    for raw_path in paths:
        try:
            text = Path(raw_path).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    return ""


def default_device_credential_store_path() -> Path:
    configured = os.environ.get(DEFAULT_STORE_ENV, "").strip()
    if configured:
        return Path(configured)
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        root = Path(local_app_data)
    else:
        root = Path.home() / "AppData" / "Local"
    return root / "auto_backup_bdnetdesk" / "credentials" / "device_credentials.json"


def _clean_base_url(value: str) -> str:
    cleaned = value.strip().rstrip("/")
    if not cleaned:
        raise DeviceCredentialStoreError("cloud_api_base_url is required")
    return cleaned


def _clean_required(value: str, field: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise DeviceCredentialStoreError(f"{field} is required")
    return cleaned


def _is_sha256_hex(value: str) -> bool:
    if len(value) != 64:
        return False
    return all(("0" <= char <= "9") or ("a" <= char <= "f") for char in value)


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    if not value:
        raise DeviceCredentialStoreError("base64url value is required")
    padded = value + ("=" * ((4 - len(value) % 4) % 4))
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except Exception as exc:
        raise DeviceCredentialStoreError("invalid base64url value") from exc


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
        raise DeviceCredentialStoreError("Windows DPAPI is only available on Windows")
    return _crypt_protect_data(data, protect=True)


def _dpapi_unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        raise DeviceCredentialStoreError("Windows DPAPI is only available on Windows")
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
    entropy_bytes = b"auto_backup_bdnetdesk.device_credentials.v1"
    entropy_buffer = ctypes.create_string_buffer(entropy_bytes)
    entropy_blob = _DataBlob(len(entropy_bytes), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_byte)))
    flags = 0x1
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
        raise DeviceCredentialStoreError(f"Windows DPAPI operation failed with error {ctypes.get_last_error()}")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)
