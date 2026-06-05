from datetime import datetime, timezone

import pytest

from auto_backup_client.baidu.crypto import (
    BAIDU_ENCRYPTION_PASSWORD,
    Argon2idParams,
    PlainBaiduToken,
    TokenEnvelopeError,
    decrypt_token_envelope,
    derive_password_wrapping_key,
    encrypt_password_token,
)


def test_password_token_envelope_round_trip() -> None:
    key = bytes([7]) * 32
    plain = PlainBaiduToken(
        access_token="fake-access-token",
        refresh_token="fake-refresh-token",
        token_type="Bearer",
        scope="basic,netdisk",
        expires_at=datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc),
    )

    encrypted = encrypt_password_token(plain, key)
    decrypted = decrypt_token_envelope(
        encrypted,
        encryption_method=BAIDU_ENCRYPTION_PASSWORD,
        password_wrapping_key=key,
    )

    assert decrypted == plain
    assert "fake-access-token" not in str(encrypted)
    assert "fake-refresh-token" not in str(encrypted)


def test_password_wrapping_key_uses_argon2id() -> None:
    params = Argon2idParams(
        salt=b"0123456789abcdef",
        time_cost=1,
        memory_cost_kib=8,
        parallelism=1,
    )

    first = derive_password_wrapping_key("backup-password", params)
    second = derive_password_wrapping_key("backup-password", params)

    assert first == second
    assert len(first) == 32


def test_envelope_method_mismatch_is_rejected() -> None:
    encrypted = encrypt_password_token(
        PlainBaiduToken(
            access_token="fake-access-token",
            refresh_token="fake-refresh-token",
            expires_at=datetime.now(timezone.utc),
        ),
        bytes([1]) * 32,
    )

    with pytest.raises(TokenEnvelopeError):
        decrypt_token_envelope(encrypted, encryption_method="rsa_oaep_sha256_aes256gcm_v1", password_wrapping_key=bytes([1]) * 32)
