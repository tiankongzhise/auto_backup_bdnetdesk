from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


JSONDict = dict[str, Any]


@dataclass(frozen=True)
class DeviceRegistration:
    device_id: str
    device_token: str

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "DeviceRegistration":
        return cls(
            device_id=str(data.get("device_id", "")),
            device_token=str(data.get("device_token", "")),
        )


@dataclass(frozen=True)
class DeviceInfo:
    device_id: str
    device_name: str = ""
    hostname: str = ""
    os_version: str = ""
    client_version: str = ""

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "DeviceInfo":
        return cls(
            device_id=str(data.get("device_id", "")),
            device_name=str(data.get("device_name", "")),
            hostname=str(data.get("hostname", "")),
            os_version=str(data.get("os_version", "")),
            client_version=str(data.get("client_version", "")),
        )


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
    device_id: str = ""
    current_device: bool = False
    baidu_uk: str = ""
    private_key_hint: str = ""
    last_verify_status: str = ""

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "BaiduAccount":
        return cls(
            account_id=str(data.get("account_id", "")),
            device_id=str(data.get("device_id", "")),
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
            current_device=bool(data.get("current_device", data.get("selected", False))),
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


@dataclass(frozen=True)
class ContentObject:
    content_id: str
    file_sha256: str
    size_bytes: int
    latest_entity_id: str
    updated_at: datetime

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "ContentObject":
        return cls(
            content_id=str(data.get("content_id", "")),
            file_sha256=str(data.get("file_sha256", "")),
            size_bytes=int(data.get("size_bytes", 0) or 0),
            latest_entity_id=str(data.get("latest_entity_id", "")),
            updated_at=parse_datetime(str(data["updated_at"])),
        )


@dataclass(frozen=True)
class BackupHistoryEntity:
    entity_id: str
    entity_type: str
    data_version: int
    revision_id: str
    canonical_record_sha256: str
    updated_by_device_id: str
    payload: JSONDict

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "BackupHistoryEntity":
        payload = data.get("payload", {})
        if not isinstance(payload, dict):
            raise ValueError("backup history payload must be a JSON object")
        return cls(
            entity_id=str(data.get("entity_id", "")),
            entity_type=str(data.get("entity_type", "")),
            data_version=int(data.get("data_version", 0) or 0),
            revision_id=str(data.get("revision_id", "")),
            canonical_record_sha256=str(data.get("canonical_record_sha256", "")),
            updated_by_device_id=str(data.get("updated_by_device_id", "")),
            payload=dict(payload),
        )


@dataclass(frozen=True)
class BackupHistoryResponse:
    device_id: str
    entities: tuple[BackupHistoryEntity, ...]

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "BackupHistoryResponse":
        raw_entities = data.get("entities", [])
        if not isinstance(raw_entities, list):
            raise ValueError("backup history entities must be a list")
        return cls(
            device_id=str(data.get("device_id", "")),
            entities=tuple(BackupHistoryEntity.from_json(item) for item in raw_entities),
        )


@dataclass(frozen=True)
class SyncRevisionEvent:
    event_id: str
    entity_type: str
    entity_id: str
    revision_id: str
    schema_version: int
    data_version: int
    operation: str
    canonical_record_sha256: str
    payload: JSONDict
    updated_at: str
    deleted_at: str | None = None

    def to_json(self) -> JSONDict:
        data: JSONDict = {
            "event_id": self.event_id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "revision_id": self.revision_id,
            "schema_version": self.schema_version,
            "data_version": self.data_version,
            "operation": self.operation,
            "canonical_record_sha256": self.canonical_record_sha256,
            "payload": dict(self.payload),
            "updated_at": self.updated_at,
        }
        if self.deleted_at:
            data["deleted_at"] = self.deleted_at
        return data


@dataclass(frozen=True)
class SyncRevisionResult:
    event_id: str
    entity_id: str
    revision_id: str
    status: str
    reason: str = ""
    cloud_data_version: int = 0
    cloud_revision_id: str = ""

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "SyncRevisionResult":
        return cls(
            event_id=str(data.get("event_id", "")),
            entity_id=str(data.get("entity_id", "")),
            revision_id=str(data.get("revision_id", "")),
            status=str(data.get("status", "")),
            reason=str(data.get("reason", "")),
            cloud_data_version=int(data.get("cloud_data_version", 0) or 0),
            cloud_revision_id=str(data.get("cloud_revision_id", "")),
        )


@dataclass(frozen=True)
class RevisionSummary:
    event_id: str
    revision_id: str
    data_version: int
    apply_status: str
    canonical_record_sha256: str
    created_at: datetime

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "RevisionSummary":
        return cls(
            event_id=str(data.get("event_id", "")),
            revision_id=str(data.get("revision_id", "")),
            data_version=int(data.get("data_version", 0) or 0),
            apply_status=str(data.get("apply_status", "")),
            canonical_record_sha256=str(data.get("canonical_record_sha256", "")),
            created_at=parse_datetime(str(data["created_at"])),
        )


@dataclass(frozen=True)
class EntitySummary:
    entity_id: str
    entity_type: str
    data_version: int
    revision_id: str
    canonical_record_sha256: str
    updated_by_device_id: str
    deleted_at: datetime | None
    recent_revisions: tuple[RevisionSummary, ...]

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "EntitySummary":
        raw_revisions = data.get("recent_revisions", [])
        revisions = tuple(RevisionSummary.from_json(item) for item in raw_revisions) if isinstance(raw_revisions, list) else tuple()
        deleted_at = data.get("deleted_at")
        return cls(
            entity_id=str(data.get("entity_id", "")),
            entity_type=str(data.get("entity_type", "")),
            data_version=int(data.get("data_version", 0) or 0),
            revision_id=str(data.get("revision_id", "")),
            canonical_record_sha256=str(data.get("canonical_record_sha256", "")),
            updated_by_device_id=str(data.get("updated_by_device_id", "")),
            deleted_at=parse_datetime(str(deleted_at)) if deleted_at else None,
            recent_revisions=revisions,
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
