from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


JSONDict = dict[str, Any]


@dataclass(frozen=True)
class BaiduAuthSession:
    session_id: str
    flow: str
    status: str
    scope: str
    encryption_method: str
    expires_at: datetime
    user_code: str = ""
    verification_url: str = ""
    qrcode_url: str = ""
    auth_url: str = ""
    account_id: str = ""
    error_code: str = ""

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "BaiduAuthSession":
        return cls(
            session_id=str(data.get("session_id", "")),
            flow=str(data.get("flow", "")),
            status=str(data.get("status", "")),
            scope=str(data.get("scope", "")),
            encryption_method=str(data.get("encryption_method", "")),
            expires_at=parse_datetime(str(data["expires_at"])),
            user_code=str(data.get("user_code", "")),
            verification_url=str(data.get("verification_url", "")),
            qrcode_url=str(data.get("qrcode_url", "")),
            auth_url=str(data.get("auth_url", "")),
            account_id=str(data.get("account_id", "")),
            error_code=str(data.get("error_code", "")),
        )


@dataclass(frozen=True)
class BaiduAccount:
    account_id: str
    display_name: str
    baidu_uid: str
    scope: str
    token_expires_at: datetime
    token_valid: bool
    encryption_method: str
    token_version: int
    selected: bool
    baidu_uk: str = ""
    private_key_hint: str = ""
    last_verify_status: str = ""

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "BaiduAccount":
        return cls(
            account_id=str(data.get("account_id", "")),
            display_name=str(data.get("display_name", "")),
            baidu_uid=str(data.get("baidu_uid", "")),
            baidu_uk=str(data.get("baidu_uk", "")),
            scope=str(data.get("scope", "")),
            token_expires_at=parse_datetime(str(data["token_expires_at"])),
            token_valid=bool(data.get("token_valid", False)),
            encryption_method=str(data.get("encryption_method", "")),
            private_key_hint=str(data.get("private_key_hint", "")),
            token_version=int(data.get("token_version", 0)),
            selected=bool(data.get("selected", False)),
            last_verify_status=str(data.get("last_verify_status", "")),
        )


@dataclass(frozen=True)
class BaiduEncryptedToken:
    account_id: str
    encryption_method: str
    token_version: int
    token_expires_at: datetime
    encrypted_token_json: JSONDict
    private_key_hint: str = ""

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "BaiduEncryptedToken":
        encrypted = data.get("encrypted_token_json")
        if not isinstance(encrypted, dict):
            raise ValueError("encrypted_token_json must be a JSON object")
        return cls(
            account_id=str(data.get("account_id", "")),
            encryption_method=str(data.get("encryption_method", "")),
            private_key_hint=str(data.get("private_key_hint", "")),
            token_version=int(data.get("token_version", 0)),
            token_expires_at=parse_datetime(str(data["token_expires_at"])),
            encrypted_token_json=dict(encrypted),
        )


@dataclass(frozen=True)
class CompleteAuthResult:
    session: BaiduAuthSession
    account: BaiduAccount
    token: BaiduEncryptedToken

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "CompleteAuthResult":
        return cls(
            session=BaiduAuthSession.from_json(_mapping(data, "session")),
            account=BaiduAccount.from_json(_mapping(data, "account")),
            token=BaiduEncryptedToken.from_json(_mapping(data, "token")),
        )


@dataclass(frozen=True)
class BaiduRefreshLease:
    acquired: bool
    account_id: str
    lease_id: str
    holder_device_id: str
    expires_at: datetime | None

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "BaiduRefreshLease":
        expires_at = data.get("expires_at")
        return cls(
            acquired=bool(data.get("acquired", False)),
            account_id=str(data.get("account_id", "")),
            lease_id=str(data.get("lease_id", "")),
            holder_device_id=str(data.get("holder_device_id", "")),
            expires_at=parse_datetime(str(expires_at)) if expires_at else None,
        )


def parse_datetime(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _mapping(data: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = data.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be a JSON object")
    return value
