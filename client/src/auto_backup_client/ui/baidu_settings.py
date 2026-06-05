from __future__ import annotations

import io
import sys
import threading
import traceback
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

import httpx
import qrcode
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from auto_backup_client.baidu.auth_workflow import (
    AuthSessionState,
    BaiduAuthWorkflow,
    PasswordAuthCompletion,
    PasswordTokenDecryption,
    PasswordWrappingMaterial,
    session_status_label,
    token_validity_label,
)
from auto_backup_client.baidu.cloud_api import BaiduCloudClient
from auto_backup_client.baidu.kdf_store import PasswordKDFRecord
from auto_backup_client.baidu.models import BaiduAccount
from auto_backup_client.device_credentials import resolve_or_register_device_credentials
from auto_backup_client.settings import ClientSettings


T = TypeVar("T")


@dataclass(frozen=True)
class BaiduSettingsPageConfig:
    cloud_api_base_url: str
    device_token: str
    device_id: str = ""
    device_credential_source: str = ""
    poll_interval_seconds: int = 5


class WorkerSignals(QObject, Generic[T]):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()


class Worker(QRunnable, Generic[T]):
    def __init__(self, task: Callable[[], T]) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self._task = task
        self.signals: WorkerSignals[T] = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.succeeded.emit(self._task())
        except Exception as exc:
            detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            self.signals.failed.emit(detail)
        finally:
            self.signals.finished.emit()


class BaiduSettingsPage(QWidget):
    def __init__(self, config: BaiduSettingsPageConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._cloud_client = BaiduCloudClient(config.cloud_api_base_url, config.device_token, timeout=15.0)
        self._workflow = BaiduAuthWorkflow(self._cloud_client)
        self._api_lock = threading.Lock()
        self._thread_pool = QThreadPool.globalInstance()
        self._workers: list[Worker[object]] = []
        self._accounts: list[BaiduAccount] = []
        self._session: AuthSessionState | None = None
        self._last_material: PasswordWrappingMaterial | None = None
        self._last_kdf_record: PasswordKDFRecord | None = None
        self._completion_in_progress = False
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(max(1, config.poll_interval_seconds) * 1000)
        self._poll_timer.timeout.connect(self.poll_current_session)

        self.setWindowTitle("百度网盘授权设置")
        self._build_ui()
        self.load_accounts()

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self._poll_timer.stop()
        self._cloud_client.close()
        super().closeEvent(event)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        header = QLabel("百度网盘授权")
        header.setObjectName("pageTitle")
        layout.addWidget(header)

        layout.addWidget(self._build_connection_group())
        layout.addWidget(self._build_accounts_group(), stretch=2)
        layout.addWidget(self._build_auth_group(), stretch=3)
        layout.addWidget(self._build_transfer_group())

        self.status_label = QLabel("准备就绪")
        layout.addWidget(self.status_label)

        self.setStyleSheet(
            """
            QWidget { font-size: 13px; }
            QLabel#pageTitle { font-size: 22px; font-weight: 600; }
            QGroupBox { font-weight: 600; border: 1px solid #c7cdd4; border-radius: 6px; margin-top: 8px; padding-top: 12px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
            QPushButton { min-height: 30px; padding: 4px 10px; }
            QLineEdit, QComboBox, QSpinBox { min-height: 28px; }
            QTableWidget { gridline-color: #d7dde3; selection-background-color: #d8e8ff; }
            """
        )

    def _build_connection_group(self) -> QGroupBox:
        group = QGroupBox("云端连接")
        form = QFormLayout(group)
        self.base_url_label = QLabel(self._config.cloud_api_base_url)
        self.device_token_label = QLabel("已加载（不显示明文）")
        self.device_source_label = QLabel(self._config.device_credential_source or "运行环境")
        form.addRow("云端 API", self.base_url_label)
        form.addRow("Device Token", self.device_token_label)
        form.addRow("凭据来源", self.device_source_label)
        if self._config.device_id:
            form.addRow("Device ID", QLabel(self._config.device_id))
        return group

    def _build_accounts_group(self) -> QGroupBox:
        group = QGroupBox("已授权账号")
        layout = QVBoxLayout(group)

        toolbar = QHBoxLayout()
        self.reload_button = QPushButton("刷新账号")
        self.reload_button.clicked.connect(self.load_accounts)
        self.select_button = QPushButton("选择账号")
        self.select_button.clicked.connect(self.select_current_account)
        self.verify_token_button = QPushButton("验证解密")
        self.verify_token_button.clicked.connect(self.verify_current_token_decryption)
        toolbar.addWidget(self.reload_button)
        toolbar.addWidget(self.select_button)
        toolbar.addWidget(self.verify_token_button)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self.accounts_table = QTableWidget(0, 7)
        self.accounts_table.setHorizontalHeaderLabels(["选择", "显示名", "百度 UID", "Scope", "Token", "版本", "校验"])
        self.accounts_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.accounts_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.accounts_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.accounts_table.verticalHeader().setVisible(False)
        layout.addWidget(self.accounts_table)

        return group

    def _build_auth_group(self) -> QGroupBox:
        group = QGroupBox("扫码确认授权")
        layout = QVBoxLayout(group)

        controls = QHBoxLayout()
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("用于本地派生 token wrapping key")
        self.start_auth_button = QPushButton("生成扫码授权")
        self.start_auth_button.clicked.connect(self.start_device_authorization)
        self.open_url_button = QPushButton("打开授权页")
        self.open_url_button.clicked.connect(self.open_authorization_url)
        self.complete_button = QPushButton("检查并保存授权")
        self.complete_button.clicked.connect(self.complete_current_session)
        controls.addWidget(QLabel("授权密码"))
        controls.addWidget(self.password_input, stretch=1)
        controls.addWidget(self.start_auth_button)
        controls.addWidget(self.open_url_button)
        controls.addWidget(self.complete_button)
        layout.addLayout(controls)

        session_row = QHBoxLayout()
        self.session_status_label = QLabel("未创建授权 session")
        self.expires_label = QLabel("")
        session_row.addWidget(self.session_status_label)
        session_row.addWidget(self.expires_label)
        session_row.addStretch(1)
        layout.addLayout(session_row)

        qr_row = QHBoxLayout()
        self.qrcode_label = QLabel()
        self.qrcode_label.setFixedSize(240, 240)
        self.qrcode_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qrcode_label.setStyleSheet("border: 1px solid #c7cdd4; background: #ffffff;")
        self.url_text = QTextEdit()
        self.url_text.setReadOnly(True)
        self.url_text.setMaximumHeight(220)
        qr_row.addWidget(self.qrcode_label)
        qr_row.addWidget(self.url_text, stretch=1)
        layout.addLayout(qr_row)

        return group

    def _build_transfer_group(self) -> QGroupBox:
        group = QGroupBox("上传参数")
        form = QFormLayout(group)
        self.root_dir_input = QLineEdit("/apps/auto_backup_bdnetdesk/backups")
        self.part_size_combo = QComboBox()
        self.part_size_combo.addItems(["4 MiB", "16 MiB", "32 MiB"])
        self.max_archive_combo = QComboBox()
        self.max_archive_combo.addItems(["3.8 GiB", "9.5 GiB", "19 GiB"])
        self.archive_concurrency = QSpinBox()
        self.archive_concurrency.setRange(1, 8)
        self.archive_concurrency.setValue(2)
        self.part_concurrency = QSpinBox()
        self.part_concurrency.setRange(1, 16)
        self.part_concurrency.setValue(4)
        form.addRow("备份根目录", self.root_dir_input)
        form.addRow("分片大小", self.part_size_combo)
        form.addRow("最大压缩包", self.max_archive_combo)
        form.addRow("同时上传 archive 数", self.archive_concurrency)
        form.addRow("单 archive 分片并发", self.part_concurrency)
        return group

    @Slot()
    def load_accounts(self) -> None:
        self._run_task(
            self._workflow.load_accounts,
            self._on_accounts_loaded,
            "正在读取真实云端账号列表...",
            busy_buttons=(self.reload_button, self.select_button, self.verify_token_button),
        )

    @Slot()
    def select_current_account(self) -> None:
        row = self.accounts_table.currentRow()
        if row < 0 or row >= len(self._accounts):
            self._show_warning("请先选择一个账号。")
            return
        account = self._accounts[row]
        self._run_task(
            lambda: self._workflow.select_account(account.account_id),
            self._on_account_selected,
            "正在选择真实云端账号...",
            busy_buttons=(self.reload_button, self.select_button, self.verify_token_button),
        )

    @Slot()
    def verify_current_token_decryption(self) -> None:
        row = self.accounts_table.currentRow()
        if row < 0 or row >= len(self._accounts):
            self._show_warning("请先选择一个账号。")
            return
        password = self.password_input.text()
        if not password:
            self._show_warning("请先输入授权密码。")
            return
        account = self._accounts[row]
        self._run_task(
            lambda: self._workflow.decrypt_password_token(account.account_id, authorization_password=password),
            self._on_token_decrypted,
            "正在读取云端密文 token 并验证本机 KDF 参数...",
            busy_buttons=(self.reload_button, self.select_button, self.verify_token_button),
        )

    @Slot()
    def start_device_authorization(self) -> None:
        if not self.password_input.text():
            self._show_warning("请先输入授权密码，用于本地派生 wrapping key。")
            return
        self._completion_in_progress = False
        self._run_task(
            self._workflow.start_device_code_session,
            self._on_session_started,
            "正在向真实云端创建设备码授权 session...",
            busy_buttons=(self.start_auth_button,),
        )

    @Slot()
    def poll_current_session(self) -> None:
        if not self._session:
            return
        session_id = self._session.session.session_id
        self._run_task(lambda: self._workflow.poll_session(session_id), self._on_session_polled, "正在轮询授权状态...")

    @Slot()
    def complete_current_session(self) -> None:
        if not self._session:
            self._show_warning("请先创建设备码授权 session。")
            return
        if self._completion_in_progress:
            return
        password = self.password_input.text()
        if not password:
            self._show_warning("请先输入授权密码。")
            return
        session_id = self._session.session.session_id
        self._completion_in_progress = True
        self._run_task(
            lambda: self._workflow.complete_password_session(session_id, authorization_password=password),
            self._on_session_completed,
            "正在完成授权并提交密文 token 到真实云端...",
            busy_buttons=(self.complete_button,),
        )

    @Slot()
    def open_authorization_url(self) -> None:
        if not self._session or not self._session.user_action_url:
            self._show_warning("当前没有可打开的授权地址。")
            return
        webbrowser.open(self._session.user_action_url)

    def _run_task(
        self,
        task: Callable[[], T],
        on_success: Callable[[T], None],
        busy_text: str,
        *,
        busy_buttons: tuple[QPushButton, ...] = (),
    ) -> None:
        self.status_label.setText(busy_text)
        self._set_buttons_enabled(busy_buttons, False)

        def locked_task() -> T:
            with self._api_lock:
                return task()

        worker: Worker[T] = Worker(locked_task)
        self._workers.append(worker)
        worker.signals.succeeded.connect(lambda result: self._finish_success(result, on_success, busy_buttons))
        worker.signals.failed.connect(lambda message: self._finish_failure(message, busy_buttons))
        worker.signals.finished.connect(lambda worker=worker: self._forget_worker(worker))
        self._thread_pool.start(worker)

    def _finish_success(
        self,
        result: object,
        on_success: Callable[[object], None],
        busy_buttons: tuple[QPushButton, ...],
    ) -> None:
        self._set_buttons_enabled(busy_buttons, True)
        on_success(result)

    def _finish_failure(self, message: str, busy_buttons: tuple[QPushButton, ...]) -> None:
        self._set_buttons_enabled(busy_buttons, True)
        if self._completion_in_progress and self._session and self._session.session.status in {"pending", "authorized"}:
            self._completion_in_progress = False
        self.status_label.setText(f"操作失败：{message}")
        QMessageBox.warning(self, "操作失败", message)

    @staticmethod
    def _set_buttons_enabled(buttons: tuple[QPushButton, ...], enabled: bool) -> None:
        for button in buttons:
            button.setEnabled(enabled)

    def _forget_worker(self, worker: Worker[object]) -> None:
        if worker in self._workers:
            self._workers.remove(worker)

    def _on_accounts_loaded(self, accounts: list[BaiduAccount]) -> None:
        self._accounts = accounts
        self.accounts_table.setRowCount(len(accounts))
        for row, account in enumerate(accounts):
            values = [
                "当前设备" if account.selected else "",
                account.display_name,
                account.baidu_uid,
                account.scope,
                token_validity_label(account),
                str(account.token_version),
                account.last_verify_status,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if account.selected:
                    item.setBackground(Qt.GlobalColor.lightGray)
                self.accounts_table.setItem(row, col, item)
        self.status_label.setText(f"已读取 {len(accounts)} 个真实云端账号。")

    def _on_account_selected(self, account: BaiduAccount) -> None:
        self.status_label.setText(f"已选择账号：{account.display_name or account.baidu_uid}")
        self.load_accounts()

    def _on_token_decrypted(self, decrypted: PasswordTokenDecryption) -> None:
        kdf_updated_at = decrypted.kdf_record.updated_at.isoformat() if decrypted.kdf_record.updated_at else "未知"
        self.status_label.setText(
            "本机 KDF 参数验证通过："
            f"account={decrypted.encrypted.account_id}，"
            f"token_version={decrypted.encrypted.token_version}，"
            f"expires={decrypted.encrypted.token_expires_at.isoformat()}，"
            f"kdf_updated={kdf_updated_at}"
        )

    def _on_session_started(self, state: AuthSessionState) -> None:
        self._session = state
        self._render_session_state()
        self._poll_timer.start()
        self.status_label.setText("扫码授权已创建，请用百度 App 扫码并确认授权。")

    def _on_session_polled(self, state: AuthSessionState) -> None:
        self._session = state
        self._render_session_state()
        if state.terminal:
            self._poll_timer.stop()
        elif state.session.status == "authorized":
            self.complete_current_session()
        self.status_label.setText(f"授权状态：{session_status_label(state.session.status)}")

    def _on_session_completed(self, completion: PasswordAuthCompletion) -> None:
        result = completion.result
        self._completion_in_progress = False
        self._last_material = completion.material
        self._last_kdf_record = completion.kdf_record
        self._session = AuthSessionState(
            session=result.session,
            can_complete=False,
            terminal=True,
            user_action_url=result.session.verification_url or result.session.auth_url,
        )
        self._render_session_state()
        self._poll_timer.stop()
        self.status_label.setText(
            f"授权完成，已选择账号：{result.account.display_name or result.account.baidu_uid}；本机已保存 KDF 参数。"
        )
        self.load_accounts()

    def _render_session_state(self) -> None:
        if not self._session:
            return
        session = self._session.session
        self.session_status_label.setText(session_status_label(session.status))
        self.expires_label.setText(f"过期时间：{session.expires_at.isoformat()}")
        action_url = _preferred_action_url(self._session)
        qr_source = _preferred_qr_source(self._session)
        lines = [
            f"session_id: {session.session_id}",
            f"status: {session.status}",
            f"scope: {session.scope}",
            f"authorization_url: {action_url}",
        ]
        self.url_text.setPlainText("\n".join(line for line in lines if line.split(": ", 1)[1]))
        self._render_qrcode(qr_source)

    def _render_qrcode(self, value: str) -> None:
        if not value:
            self.qrcode_label.setText("无二维码")
            self.qrcode_label.setPixmap(QPixmap())
            return
        pixmap = QPixmap()
        if _looks_like_url(value):
            try:
                with httpx.Client(timeout=10.0, follow_redirects=True) as client:
                    response = client.get(value)
                content_type = response.headers.get("content-type", "")
                if response.status_code < 400 and "image" in content_type:
                    pixmap.loadFromData(response.content)
            except Exception:
                pixmap = QPixmap()
        if pixmap.isNull():
            image = qrcode.make(value)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            pixmap.loadFromData(buffer.getvalue(), "PNG")
        self.qrcode_label.setPixmap(
            pixmap.scaled(
                self.qrcode_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _show_warning(self, message: str) -> None:
        QMessageBox.warning(self, "提示", message)


def run_baidu_settings_app(settings: ClientSettings | None = None) -> int:
    loaded = settings or ClientSettings.from_env()
    loaded.validate(require_device_token=False)
    credentials, source = resolve_or_register_device_credentials(
        cloud_api_base_url=loaded.cloud_api_base_url,
        provided_device_token=loaded.device_token,
    )
    app = QApplication.instance() or QApplication(sys.argv)
    page = BaiduSettingsPage(
        BaiduSettingsPageConfig(
            cloud_api_base_url=loaded.cloud_api_base_url,
            device_token=credentials.device_token,
            device_id=credentials.device_id,
            device_credential_source=source,
        )
    )
    page.resize(1040, 820)
    page.show()
    return app.exec()


def _preferred_action_url(state: AuthSessionState) -> str:
    session = state.session
    return session.auth_url or session.verification_url or state.user_action_url or session.qrcode_url


def _preferred_qr_source(state: AuthSessionState) -> str:
    session = state.session
    return session.qrcode_url or session.auth_url or state.user_action_url or session.verification_url


def _looks_like_url(value: str) -> bool:
    lowered = value.strip().lower()
    return lowered.startswith("https://") or lowered.startswith("http://")


if __name__ == "__main__":
    raise SystemExit(run_baidu_settings_app())
