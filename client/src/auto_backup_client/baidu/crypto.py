from __future__ import annotations

import base64
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from auto_backup_client.baidu.models import parse_datetime


BAIDU_ENCRYPTION_PASSWORD = "password_argon2id_aes256gcm_v1"
BAIDU_ENCRYPTION_RSA = "rsa_oaep_sha256_aes256gcm_v1"


class TokenEnvelopeError(ValueError):
    pass


@dataclass(frozen=True)
class Argon2idParams:
    salt: bytes
    time_cost: int = 3
    memory_cost_kib: int = 64 * 1024
    parallelism: int = 1
    hash_len: int = 32


@dataclass(frozen=True)
class PlainBaiduToken:
    access_token: str
    refresh_token: str
    expires_at: datetime
    token_type: str = "Bearer"
    scope: str = ""

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "PlainBaiduToken":
        return cls(
            access_token=str(data.get("access_token", "")),
            refresh_token=str(data.get("refresh_token", "")),
            token_type=str(data.get("token_type", "Bearer") or "Bearer"),
            scope=str(data.get("scope", "")),
            expires_at=parse_datetime(str(data["expires_at"])),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "token_type": self.token_type or "Bearer",
            "scope": self.scope,
            "expires_at": self.expires_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        }


def generate_password_salt(length: int = 16) -> bytes:
    if length < 16:
        raise ValueError("password salt must be at least 16 bytes")
    return secrets.token_bytes(length)


def derive_password_wrapping_key(password: str, params: Argon2idParams) -> bytes:
    if not password:
        raise ValueError("password is required")
    if len(params.salt) < 16:
        raise ValueError("password salt must be at least 16 bytes")
    return hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=params.salt,
        time_cost=params.time_cost,
        memory_cost=params.memory_cost_kib,
        parallelism=params.parallelism,
        hash_len=params.hash_len,
        type=Type.ID,
    )


def decrypt_token_envelope(
    encrypted_token_json: Mapping[str, Any],
    *,
    encryption_method: str,
    password_wrapping_key: bytes | None = None,
    rsa_private_key_pem: bytes | str | None = None,
    rsa_private_key_password: bytes | str | None = None,
) -> PlainBaiduToken:
    envelope = _validate_envelope(encrypted_token_json, encryption_method)
    if encryption_method == BAIDU_ENCRYPTION_PASSWORD:
        if password_wrapping_key is None:
            raise TokenEnvelopeError("password wrapping key is required")
        plaintext = _decrypt_aes_gcm(password_wrapping_key, envelope)
    elif encryption_method == BAIDU_ENCRYPTION_RSA:
        if rsa_private_key_pem is None:
            raise TokenEnvelopeError("rsa private key is required")
        content_key = _unwrap_rsa_content_key(envelope, rsa_private_key_pem, rsa_private_key_password)
        plaintext = _decrypt_aes_gcm(content_key, envelope)
    else:
        raise TokenEnvelopeError(f"unsupported encryption method: {encryption_method}")
    return PlainBaiduToken.from_json(json.loads(plaintext.decode("utf-8")))


def encrypt_password_token(token: PlainBaiduToken, wrapping_key: bytes) -> dict[str, Any]:
    if len(wrapping_key) != 32:
        raise TokenEnvelopeError("password wrapping key must be 32 bytes")
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(wrapping_key).encrypt(
        nonce,
        json.dumps(token.to_json(), separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
        None,
    )
    return {
        "version": 1,
        "encryption_method": BAIDU_ENCRYPTION_PASSWORD,
        "algorithm": "aes-256-gcm",
        "nonce": _b64url_encode(nonce),
        "ciphertext": _b64url_encode(ciphertext),
    }


def _validate_envelope(envelope: Mapping[str, Any], expected_method: str) -> Mapping[str, Any]:
    if int(envelope.get("version", 0)) != 1:
        raise TokenEnvelopeError("unsupported token envelope version")
    if str(envelope.get("encryption_method", "")) != expected_method:
        raise TokenEnvelopeError("token envelope encryption_method does not match response")
    if str(envelope.get("algorithm", "")) != "aes-256-gcm":
        raise TokenEnvelopeError("unsupported token envelope algorithm")
    if not envelope.get("nonce") or not envelope.get("ciphertext"):
        raise TokenEnvelopeError("token envelope nonce and ciphertext are required")
    return envelope


def _decrypt_aes_gcm(key: bytes, envelope: Mapping[str, Any]) -> bytes:
    if len(key) != 32:
        raise TokenEnvelopeError("token content key must be 32 bytes")
    try:
        return AESGCM(key).decrypt(
            _b64url_decode(str(envelope["nonce"])),
            _b64url_decode(str(envelope["ciphertext"])),
            None,
        )
    except Exception as exc:
        raise TokenEnvelopeError("failed to decrypt baidu token envelope") from exc


def _unwrap_rsa_content_key(
    envelope: Mapping[str, Any],
    private_key_pem: bytes | str,
    private_key_password: bytes | str | None,
) -> bytes:
    if envelope.get("wrapped_key_algorithm") != "rsa-oaep-sha256":
        raise TokenEnvelopeError("unsupported rsa wrapped key algorithm")
    wrapped_key = envelope.get("wrapped_key")
    if not wrapped_key:
        raise TokenEnvelopeError("rsa wrapped key is required")
    private_key_data = private_key_pem.encode("utf-8") if isinstance(private_key_pem, str) else private_key_pem
    password_data = (
        private_key_password.encode("utf-8")
        if isinstance(private_key_password, str)
        else private_key_password
    )
    private_key = load_pem_private_key(private_key_data, password=password_data)
    try:
        return private_key.decrypt(
            _b64url_decode(str(wrapped_key)),
            padding.OAEP(mgf=padding.MGF1(algorithm=SHA256()), algorithm=SHA256(), label=None),
        )
    except Exception as exc:
        raise TokenEnvelopeError("failed to unwrap baidu token content key") from exc


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padded = value + ("=" * ((4 - len(value) % 4) % 4))
    return base64.urlsafe_b64decode(padded.encode("ascii"))
