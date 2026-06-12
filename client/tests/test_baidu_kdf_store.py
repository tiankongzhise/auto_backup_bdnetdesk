from __future__ import annotations

import json
import os

import pytest

from auto_backup_client.baidu.crypto import Argon2idParams
from auto_backup_client.baidu.kdf_store import PasswordKDFRecord, PasswordKDFStore, PasswordKDFStoreError


def test_plaintext_kdf_store_requires_explicit_opt_in(tmp_path) -> None:
    record = _record()
    path = tmp_path / "baidu-kdf.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "protection": "plaintext_test_only_v1",
                "payload": {"version": 1, "records": {record.account_id: record.to_json()}},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(PasswordKDFStoreError, match="plaintext password KDF store is not allowed"):
        PasswordKDFStore(path).require_record(record.account_id)

    loaded = PasswordKDFStore(path, allow_plaintext=True).require_record(record.account_id)
    assert loaded.salt == record.salt


def test_plaintext_test_store_restores_same_wrapping_key(tmp_path) -> None:
    path = tmp_path / "baidu-kdf.json"
    store = PasswordKDFStore(path, allow_plaintext=True)
    saved = store.save_record(_record())

    restored = PasswordKDFStore(path, allow_plaintext=True).require_record(saved.account_id)

    assert restored.derive_wrapping_key("backup-password") == saved.derive_wrapping_key("backup-password")
    assert "backup-password" not in path.read_text(encoding="utf-8")


def test_device_scoped_records_do_not_overwrite_legacy_account_record(tmp_path) -> None:
    path = tmp_path / "baidu-kdf.json"
    store = PasswordKDFStore(path, allow_plaintext=True)
    legacy = store.save_record(_record(salt=b"0123456789abcdef"))
    first_device = store.save_record(_record(device_id="device-a", salt=b"aaaaaaaaaaaaaaaa"))
    second_device = store.save_record(_record(device_id="device-b", salt=b"bbbbbbbbbbbbbbbb"))

    restored = PasswordKDFStore(path, allow_plaintext=True)

    assert restored.require_record("bacc_1").salt == legacy.salt
    assert restored.require_record("bacc_1", device_id="device-a").salt == first_device.salt
    assert restored.require_record("bacc_1", device_id="device-b").salt == second_device.salt
    assert restored.require_record("bacc_1", device_id="unknown-device").salt == legacy.salt


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI is only available on Windows")
def test_default_windows_store_uses_dpapi(tmp_path) -> None:
    path = tmp_path / "baidu-kdf.json"
    saved = PasswordKDFStore(path).save_record(_record())
    wrapper = json.loads(path.read_text(encoding="utf-8"))

    assert wrapper["protection"] == "windows_dpapi_current_user_v1"
    assert "ciphertext" in wrapper
    assert "payload" not in wrapper
    restored = PasswordKDFStore(path).require_record(saved.account_id)
    assert restored.derive_wrapping_key("backup-password") == saved.derive_wrapping_key("backup-password")


def _record(*, device_id: str = "", salt: bytes = b"0123456789abcdef") -> PasswordKDFRecord:
    return PasswordKDFRecord.from_params(
        account_id="bacc_1",
        device_id=device_id,
        params=Argon2idParams(
            salt=salt,
            time_cost=1,
            memory_cost_kib=8,
            parallelism=1,
        ),
        token_version=3,
    )
