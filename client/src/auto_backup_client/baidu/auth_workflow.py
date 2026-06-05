from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from auto_backup_client.baidu.cloud_api import BaiduCloudClient
from auto_backup_client.baidu.crypto import (
    BAIDU_ENCRYPTION_PASSWORD,
    Argon2idParams,
    derive_password_wrapping_key,
    generate_password_salt,
)
from auto_backup_client.baidu.models import BaiduAccount, BaiduAuthSession, CompleteAuthResult


TERMINAL_SESSION_STATUSES = {"completed", "failed", "expired"}


@dataclass(frozen=True)
class PasswordWrappingMaterial:
    wrapping_key: bytes
    salt: bytes
    argon2id_time_cost: int
    argon2id_memory_cost_kib: int
    argon2id_parallelism: int

    @classmethod
    def from_password(cls, password: str, *, salt: bytes | None = None) -> "PasswordWrappingMaterial":
        params = Argon2idParams(salt=salt or generate_password_salt())
        return cls(
            wrapping_key=derive_password_wrapping_key(password, params),
            salt=params.salt,
            argon2id_time_cost=params.time_cost,
            argon2id_memory_cost_kib=params.memory_cost_kib,
            argon2id_parallelism=params.parallelism,
        )


@dataclass(frozen=True)
class AuthSessionState:
    session: BaiduAuthSession
    can_complete: bool
    terminal: bool
    user_action_url: str


class BaiduAuthWorkflow:
    def __init__(self, cloud_client: BaiduCloudClient) -> None:
        self._cloud = cloud_client

    def load_accounts(self) -> list[BaiduAccount]:
        return self._cloud.list_accounts()

    def select_account(self, account_id: str) -> BaiduAccount:
        account_id = account_id.strip()
        if not account_id:
            raise ValueError("account_id is required")
        return self._cloud.select_account(account_id)

    def start_device_code_session(self) -> AuthSessionState:
        session = self._cloud.create_auth_session(
            flow="device_code",
            encryption_method=BAIDU_ENCRYPTION_PASSWORD,
        )
        return _session_state(session)

    def poll_session(self, session_id: str) -> AuthSessionState:
        session_id = session_id.strip()
        if not session_id:
            raise ValueError("session_id is required")
        return _session_state(self._cloud.get_auth_session(session_id))

    def complete_password_session(
        self,
        session_id: str,
        *,
        authorization_password: str,
        salt: bytes | None = None,
    ) -> tuple[CompleteAuthResult, PasswordWrappingMaterial]:
        session_id = session_id.strip()
        if not session_id:
            raise ValueError("session_id is required")
        if not authorization_password:
            raise ValueError("authorization password is required")
        material = PasswordWrappingMaterial.from_password(authorization_password, salt=salt)
        result = self._cloud.complete_auth_session(session_id, wrapping_key=material.wrapping_key)
        return result, material


def session_status_label(status: str) -> str:
    labels = {
        "pending": "等待用户授权",
        "authorized": "已收到百度回调，可完成加密入库",
        "completed": "授权已完成",
        "failed": "授权失败",
        "expired": "授权已过期",
    }
    return labels.get(status, status or "未知状态")


def token_validity_label(account: BaiduAccount, *, now: datetime | None = None) -> Literal["valid", "expired", "invalid"]:
    if not account.token_valid:
        return "invalid"
    current = now or datetime.now(timezone.utc)
    if current >= account.token_expires_at:
        return "expired"
    return "valid"


def generate_ephemeral_device_name(prefix: str = "auto-backup-ui") -> str:
    return f"{prefix}-{secrets.token_hex(4)}"


def _session_state(session: BaiduAuthSession) -> AuthSessionState:
    user_action_url = session.verification_url or session.auth_url
    return AuthSessionState(
        session=session,
        can_complete=session.status in {"pending", "authorized"},
        terminal=session.status in TERMINAL_SESSION_STATUSES,
        user_action_url=user_action_url,
    )
