from __future__ import annotations

import os
from datetime import datetime, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from auto_backup_client.baidu.models import BaiduAccount
from auto_backup_client.ui import baidu_settings
from auto_backup_client.ui.baidu_settings import BaiduSettingsPage, BaiduSettingsPageConfig


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_baidu_settings_page_shows_device_id_summary_and_full_id(monkeypatch) -> None:
    _app()
    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(baidu_settings.BaiduSettingsPage, "load_accounts", lambda self: None)
    monkeypatch.setattr(QMessageBox, "information", lambda _parent, title, text: shown.append((title, text)))

    page = BaiduSettingsPage(
        BaiduSettingsPageConfig(
            cloud_api_base_url="https://backup.baichengedu.com",
            device_token="secret-device-token",
            device_id="dev-current-1234567890",
        )
    )
    page._on_accounts_loaded(
        [
            _account(
                device_id="dev-current-1234567890",
                selected=True,
                current_device=True,
            ),
            _account(
                account_id="bacc_2",
                device_id="dev-other-0987654321",
                selected=False,
                current_device=False,
            ),
        ]
    )

    assert page.accounts_table.horizontalHeaderItem(0).text() == "设备"
    assert page.accounts_table.item(0, 0).text() == "dev-...7890 (本机设备)"
    assert page.accounts_table.item(1, 0).text() == "dev-...4321"
    assert page.accounts_table.item(0, 0).text() != "当前设备"

    page._show_account_device_id(page.accounts_table.item(1, 0))

    assert shown == [("完整设备 ID", "dev-other-0987654321")]
    page.close()


def _account(
    *,
    account_id: str = "bacc_1",
    device_id: str,
    selected: bool,
    current_device: bool,
) -> BaiduAccount:
    return BaiduAccount(
        account_id=account_id,
        device_id=device_id,
        display_name="测试账号",
        baidu_uid="uid-1",
        scope="basic,netdisk",
        token_expires_at=datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc),
        token_valid=True,
        encryption_method="password_argon2id_aes256gcm_v1",
        token_version=1,
        selected=selected,
        current_device=current_device,
        last_verify_status="valid",
    )
