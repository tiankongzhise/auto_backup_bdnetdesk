from datetime import datetime, timedelta, timezone

from auto_backup_client.baidu.auth_workflow import (
    PasswordWrappingMaterial,
    session_status_label,
    token_validity_label,
)
from auto_backup_client.baidu.models import BaiduAccount


def test_password_wrapping_material_is_process_only_key_material() -> None:
    material = PasswordWrappingMaterial.from_password(
        "backup-password",
        salt=b"0123456789abcdef",
    )

    assert len(material.wrapping_key) == 32
    assert material.salt == b"0123456789abcdef"
    assert material.argon2id_memory_cost_kib == 64 * 1024


def test_session_status_label_uses_product_language() -> None:
    assert session_status_label("pending") == "等待用户授权"
    assert session_status_label("authorized") == "已收到百度回调，可完成加密入库"
    assert session_status_label("unknown") == "unknown"


def test_token_validity_label_distinguishes_invalid_and_expired() -> None:
    now = datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc)
    valid = _account(token_valid=True, expires_at=now + timedelta(minutes=1))
    expired = _account(token_valid=True, expires_at=now - timedelta(minutes=1))
    invalid = _account(token_valid=False, expires_at=now + timedelta(minutes=1))

    assert token_validity_label(valid, now=now) == "valid"
    assert token_validity_label(expired, now=now) == "expired"
    assert token_validity_label(invalid, now=now) == "invalid"


def _account(*, token_valid: bool, expires_at: datetime) -> BaiduAccount:
    return BaiduAccount(
        account_id="bacc_test",
        display_name="测试账号",
        baidu_uid="uid",
        scope="basic,netdisk",
        token_expires_at=expires_at,
        token_valid=token_valid,
        encryption_method="password_argon2id_aes256gcm_v1",
        token_version=1,
        selected=False,
    )
