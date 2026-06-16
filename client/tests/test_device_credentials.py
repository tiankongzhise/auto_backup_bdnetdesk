from __future__ import annotations

import json
import os

import pytest

from auto_backup_client.baidu.cloud_api import DeviceRegistration
from auto_backup_client.device_credentials import (
    DeviceCredentialStore,
    DeviceCredentialStoreError,
    DeviceCredentials,
    derive_stable_device_identity,
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


def test_resolve_runtime_token_reuses_store_device_id_when_token_matches(tmp_path) -> None:
    store = DeviceCredentialStore(tmp_path / "device-credentials.json", allow_plaintext=True)
    store.save(_credentials())

    credentials, source = resolve_or_register_device_credentials(
        cloud_api_base_url="https://backup.baichengedu.com",
        provided_device_token=" fake-device-token ",
        store=store,
    )

    assert credentials.device_token == "fake-device-token"
    assert credentials.device_id == "dev_1"
    assert source == "运行环境 + 本机 DPAPI 凭据"


def test_resolve_runtime_token_fetches_current_device_when_store_token_differs(tmp_path, monkeypatch) -> None:
    store = DeviceCredentialStore(tmp_path / "device-credentials.json", allow_plaintext=True)
    store.save(_credentials())
    remote = type(
        "RemoteDevice",
        (),
        {
            "device_id": "dev_remote",
            "device_name": "remote device",
            "hostname": "remote-host",
            "os_version": "Windows",
            "client_version": "v1.3",
        },
    )()
    monkeypatch.setattr("auto_backup_client.device_credentials._current_device_from_token", lambda base_url, token: remote)

    credentials, source = resolve_or_register_device_credentials(
        cloud_api_base_url="https://backup.baichengedu.com",
        provided_device_token="runtime-token",
        store=store,
    )

    assert credentials.device_token == "runtime-token"
    assert credentials.device_id == "dev_remote"
    assert credentials.device_name == "remote device"
    assert source == "运行环境 + 云端当前设备"
    assert store.load().device_token == "fake-device-token"


def test_resolve_runtime_token_fails_when_device_id_cannot_be_confirmed(tmp_path, monkeypatch) -> None:
    store = DeviceCredentialStore(tmp_path / "device-credentials.json", allow_plaintext=True)

    def fail(_base_url, _token):
        raise DeviceCredentialStoreError("failed to resolve device_id from runtime Device Token")

    monkeypatch.setattr("auto_backup_client.device_credentials._current_device_from_token", fail)

    with pytest.raises(DeviceCredentialStoreError, match="failed to resolve device_id"):
        resolve_or_register_device_credentials(
            cloud_api_base_url="https://backup.baichengedu.com",
            provided_device_token="runtime-token",
            store=store,
        )


def test_stable_device_identity_does_not_depend_on_client_version() -> None:
    features = {"windows_machine_guid": "A1B2C3D4-E5F6-4789-ABCD-001122334455"}

    identity_for_old_client = derive_stable_device_identity(features)
    identity_for_new_client = derive_stable_device_identity(dict(features))

    assert identity_for_old_client == identity_for_new_client
    assert identity_for_old_client.device_id.startswith("dev_")
    assert identity_for_old_client.fingerprint_hash


def test_resolve_new_device_registers_with_stable_local_device_id(tmp_path, monkeypatch) -> None:
    store = DeviceCredentialStore(tmp_path / "device-credentials.json", allow_plaintext=True)
    identity = derive_stable_device_identity({"windows_machine_guid": "A1B2C3D4-E5F6-4789-ABCD-001122334455"})
    captured: dict[str, str] = {}

    def fake_collect_features():
        return {"windows_machine_guid": "A1B2C3D4-E5F6-4789-ABCD-001122334455"}

    def fake_register(_base_url, **kwargs):
        captured.update({key: str(value) for key, value in kwargs.items() if isinstance(value, str)})
        return DeviceRegistration(device_id=kwargs["device_id"], device_token="new-token")

    monkeypatch.setattr("auto_backup_client.device_credentials._collect_device_identity_features", fake_collect_features)
    monkeypatch.setattr("auto_backup_client.device_credentials.register_device", fake_register)

    credentials, source = resolve_or_register_device_credentials(
        cloud_api_base_url="https://backup.baichengedu.com",
        store=store,
        client_version="2.0.0",
    )

    assert source == "新注册并保存到本机 DPAPI"
    assert credentials.device_id == identity.device_id
    assert credentials.device_fingerprint_hash == identity.fingerprint_hash
    assert captured["device_id"] == identity.device_id
    assert captured["device_fingerprint_hash"] == identity.fingerprint_hash
    assert store.load().device_id == identity.device_id


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
