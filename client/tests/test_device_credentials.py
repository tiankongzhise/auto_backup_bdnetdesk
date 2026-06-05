from __future__ import annotations

import json
import os

import pytest

from auto_backup_client.baidu.cloud_api import DeviceRegistration
from auto_backup_client.device_credentials import (
    DeviceCredentialStore,
    DeviceCredentialStoreError,
    DeviceCredentials,
    resolve_or_register_device_credentials,
)


def test_plaintext_device_store_requires_explicit_opt_in(tmp_path) -> None:
    credential = _credentials()
    path = tmp_path / "device-credentials.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "protection": "plaintext_test_only_v1",
                "payload": {"version": 1, "credential": credential.to_json()},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(DeviceCredentialStoreError, match="plaintext device credential store is not allowed"):
        DeviceCredentialStore(path).load()

    loaded = DeviceCredentialStore(path, allow_plaintext=True).load()
    assert loaded is not None
    assert loaded.device_token == "fake-device-token"


def test_plaintext_device_store_restores_saved_credentials(tmp_path) -> None:
    path = tmp_path / "device-credentials.json"
    saved = DeviceCredentialStore(path, allow_plaintext=True).save(_credentials())

    restored = DeviceCredentialStore(path, allow_plaintext=True).load_for_base_url("https://backup.baichengedu.com/")

    assert restored == saved
    assert "fake-device-token" in path.read_text(encoding="utf-8")


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI is only available on Windows")
def test_default_windows_device_store_uses_dpapi(tmp_path) -> None:
    path = tmp_path / "device-credentials.json"
    saved = DeviceCredentialStore(path).save(_credentials())
    wrapper = json.loads(path.read_text(encoding="utf-8"))

    assert wrapper["protection"] == "windows_dpapi_current_user_v1"
    assert "ciphertext" in wrapper
    assert "payload" not in wrapper
    restored = DeviceCredentialStore(path).load()
    assert restored == saved


def test_resolve_prefers_runtime_token_without_writing_store(tmp_path) -> None:
    store = DeviceCredentialStore(tmp_path / "device-credentials.json", allow_plaintext=True)

    credentials, source = resolve_or_register_device_credentials(
        cloud_api_base_url="https://backup.baichengedu.com",
        provided_device_token=" runtime-token ",
        store=store,
    )

    assert credentials.device_token == "runtime-token"
    assert source == "运行环境"
    assert not store.path.exists()


def test_resolve_reuses_saved_credentials(tmp_path) -> None:
    store = DeviceCredentialStore(tmp_path / "device-credentials.json", allow_plaintext=True)
    saved = store.save(_credentials())

    credentials, source = resolve_or_register_device_credentials(
        cloud_api_base_url="https://backup.baichengedu.com/",
        store=store,
    )

    assert credentials == saved
    assert source == "本机 DPAPI 凭据"


def _credentials() -> DeviceCredentials:
    return DeviceCredentials.from_registration(
        DeviceRegistration(device_id="dev_1", device_token="fake-device-token"),
        cloud_api_base_url="https://backup.baichengedu.com",
        device_name="test-device",
        hostname="test-host",
        os_version="test-os",
        client_version="0.1.0",
    )
