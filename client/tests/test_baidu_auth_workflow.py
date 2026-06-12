from datetime import datetime, timedelta, timezone

from auto_backup_client.baidu.auth_workflow import (
    BaiduAuthWorkflow,
    PasswordWrappingMaterial,
    session_status_label,
    token_validity_label,
)
from auto_backup_client.baidu.crypto import PlainBaiduToken, encrypt_password_token
from auto_backup_client.baidu.kdf_store import PasswordKDFStore
from auto_backup_client.baidu.models import BaiduAccount, BaiduAuthSession, BaiduEncryptedToken, CompleteAuthResult


def test_password_wrapping_material_is_process_only_key_material() -> None:
    material = PasswordWrappingMaterial.from_password(
        "backup-password",
        salt=b"0123456789abcdef",
    )

    assert len(material.wrapping_key) == 32
    assert material.salt == b"0123456789abcdef"
    assert material.argon2id_memory_cost_kib == 64 * 1024


def test_workflow_persists_kdf_material_and_restores_token_decryption_after_restart(tmp_path) -> None:
    store_path = tmp_path / "baidu-kdf.json"
    first_cloud = _FakeCloudForPasswordToken()
    first_workflow = BaiduAuthWorkflow(first_cloud, kdf_store=PasswordKDFStore(store_path, allow_plaintext=True))

    completion = first_workflow.complete_password_session(
        "session-1",
        authorization_password="backup-password",
        salt=b"0123456789abcdef",
    )

    assert completion.kdf_record.account_id == "bacc_1"
    assert completion.result.token.encrypted_token_json == first_cloud.encrypted_token_json
    assert "backup-password" not in store_path.read_text(encoding="utf-8")

    restarted_cloud = _FakeCloudForPasswordToken(encrypted_token_json=first_cloud.encrypted_token_json)
    restarted_workflow = BaiduAuthWorkflow(
        restarted_cloud,
        kdf_store=PasswordKDFStore(store_path, allow_plaintext=True),
    )

    decrypted = restarted_workflow.decrypt_password_token("bacc_1", authorization_password="backup-password")

    assert decrypted.token.access_token == "fake-access-token"
    assert decrypted.token.refresh_token == "fake-refresh-token"
    assert decrypted.encrypted.token_version == 1
    assert decrypted.kdf_record.salt == b"0123456789abcdef"


def test_workflow_saves_kdf_material_per_device(tmp_path) -> None:
    store_path = tmp_path / "baidu-kdf.json"
    first_workflow = BaiduAuthWorkflow(
        _FakeCloudForPasswordToken(),
        kdf_store=PasswordKDFStore(store_path, allow_plaintext=True),
        device_id="device-a",
    )
    second_workflow = BaiduAuthWorkflow(
        _FakeCloudForPasswordToken(),
        kdf_store=PasswordKDFStore(store_path, allow_plaintext=True),
        device_id="device-b",
    )

    first = first_workflow.complete_password_session(
        "session-a",
        authorization_password="backup-password",
        salt=b"aaaaaaaaaaaaaaaa",
    )
    second = second_workflow.complete_password_session(
        "session-b",
        authorization_password="backup-password",
        salt=b"bbbbbbbbbbbbbbbb",
    )

    assert first.kdf_record.account_id == second.kdf_record.account_id == "bacc_1"
    assert first.kdf_record.device_id == "device-a"
    assert second.kdf_record.device_id == "device-b"

    restarted_store = PasswordKDFStore(store_path, allow_plaintext=True)
    assert restarted_store.require_record("bacc_1", device_id="device-a").salt == b"aaaaaaaaaaaaaaaa"
    assert restarted_store.require_record("bacc_1", device_id="device-b").salt == b"bbbbbbbbbbbbbbbb"


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


def _account(*, token_valid: bool, expires_at: datetime, account_id: str = "bacc_test") -> BaiduAccount:
    return BaiduAccount(
        account_id=account_id,
        display_name="测试账号",
        baidu_uid="uid",
        scope="basic,netdisk",
        token_expires_at=expires_at,
        token_valid=token_valid,
        encryption_method="password_argon2id_aes256gcm_v1",
        token_version=1,
        selected=False,
    )


class _FakeCloudForPasswordToken:
    def __init__(self, encrypted_token_json: dict[str, object] | None = None) -> None:
        self.encrypted_token_json = encrypted_token_json or {}

    def complete_auth_session(self, session_id: str, *, wrapping_key: bytes) -> CompleteAuthResult:
        plain = PlainBaiduToken(
            access_token="fake-access-token",
            refresh_token="fake-refresh-token",
            scope="basic,netdisk",
            expires_at=datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc),
        )
        self.encrypted_token_json = encrypt_password_token(plain, wrapping_key)
        token = self._encrypted_token()
        account = _account(token_valid=True, expires_at=plain.expires_at, account_id="bacc_1")
        return CompleteAuthResult(
            session=BaiduAuthSession(
                session_id=session_id,
                flow="device_code",
                status="completed",
                scope="basic,netdisk",
                encryption_method="password_argon2id_aes256gcm_v1",
                expires_at=plain.expires_at,
                account_id="bacc_1",
            ),
            account=account,
            token=token,
        )

    def get_token(self, account_id: str) -> BaiduEncryptedToken:
        assert account_id == "bacc_1"
        return self._encrypted_token()

    def _encrypted_token(self) -> BaiduEncryptedToken:
        return BaiduEncryptedToken(
            account_id="bacc_1",
            encryption_method="password_argon2id_aes256gcm_v1",
            token_version=1,
            token_expires_at=datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc),
            encrypted_token_json=dict(self.encrypted_token_json),
        )
