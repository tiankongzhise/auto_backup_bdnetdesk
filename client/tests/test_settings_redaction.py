from auto_backup_client.redaction import REDACTED, redact
from auto_backup_client.settings import ClientSettings


def test_settings_from_env_trims_values() -> None:
    settings = ClientSettings.from_env(
        {
            "CLOUD_API_BASE_URL": " https://backup.baichengedu.com ",
            "CLOUD_API_DEVICE_TOKEN": " fake-device-token ",
            "BAIDU_TOKEN_URL": " https://openapi.baidu.com/oauth/2.0/token ",
        }
    )

    settings.validate()

    assert settings.cloud_api_base_url == "https://backup.baichengedu.com"
    assert settings.device_token == "fake-device-token"


def test_redaction_masks_nested_sensitive_fields() -> None:
    value = {
        "device_token": "fake-device-token",
        "nested": {
            "access_token": "fake-access-token",
            "safe": "visible",
        },
        "items": [{"refresh_token": "fake-refresh-token"}],
    }

    assert redact(value) == {
        "device_token": REDACTED,
        "nested": {
            "access_token": REDACTED,
            "safe": "visible",
        },
        "items": [{"refresh_token": REDACTED}],
    }
