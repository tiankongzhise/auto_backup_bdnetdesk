from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QMimeData, Qt, Signal, Slot
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from auto_backup_client.backup_jobs import (
    BackupJobError,
    BackupJobManager,
    BackupJobWithSources,
    BackupSourceInput,
    BackupSourceType,
    status_label,
)
from auto_backup_client.device_credentials import resolve_or_register_device_credentials
from auto_backup_client.settings import ClientSettings
from auto_backup_client.sqlite_store import SQLiteClientStore
from auto_backup_client.ui.baidu_settings import BaiduSettingsPage, BaiduSettingsPageConfig


@dataclass(frozen=True)
class MainWindowConfig:
    cloud_api_base_url: str
    device_token: str
    device_id: str
    sqlite_path: str
    device_credential_source: str = ""


@dataclass(frozen=True)
class PendingSource:
    local_path: str
    source_type: BackupSourceType


class BackupTaskPage(QWidget):
    status_changed = Signal(str)

    def __init__(self, manager: BackupJobManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._manager = manager
        self._pending_sources: list[PendingSource] = []
        self._jobs: list[BackupJobWithSources] = []
        self.setAcceptDrops(True)
        self._build_ui()
        self.refresh_jobs()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if _mime_has_local_files(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        paths = _local_paths_from_mime(event.mimeData())
        if paths:
            self.add_sources(paths)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = QLabel("备份任务")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        layout.addWidget(self._build_source_group())
        layout.addWidget(self._build_jobs_group(), stretch=1)

    def _build_source_group(self) -> QGroupBox:
        group = QGroupBox("新建任务")
        layout = QVBoxLayout(group)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("任务名称"))
        self.job_name_input = QLineEdit()
        self.job_name_input.setPlaceholderText("留空时使用创建时间")
        name_row.addWidget(self.job_name_input, stretch=1)
        self.add_files_button = QPushButton("选择文件")
        self.add_files_button.clicked.connect(self.choose_files)
        self.add_folder_button = QPushButton("选择文件夹")
        self.add_folder_button.clicked.connect(self.choose_directory)
        name_row.addWidget(self.add_files_button)
        name_row.addWidget(self.add_folder_button)
        layout.addLayout(name_row)

        self.pending_table = QTableWidget(0, 3)
        self.pending_table.setHorizontalHeaderLabels(["类型", "名称", "路径指纹"])
        self.pending_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.pending_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pending_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.pending_table.verticalHeader().setVisible(False)
        self.pending_table.setMinimumHeight(150)
        layout.addWidget(self.pending_table)

        toolbar = QHBoxLayout()
        self.remove_source_button = QPushButton("移除来源")
        self.remove_source_button.clicked.connect(self.remove_selected_pending_source)
        self.clear_sources_button = QPushButton("清空来源")
        self.clear_sources_button.clicked.connect(self.clear_pending_sources)
        self.create_job_button = QPushButton("创建任务")
        self.create_job_button.clicked.connect(self.create_job)
        toolbar.addWidget(QLabel("可拖拽文件或文件夹到此页"))
        toolbar.addStretch(1)
        toolbar.addWidget(self.remove_source_button)
        toolbar.addWidget(self.clear_sources_button)
        toolbar.addWidget(self.create_job_button)
        layout.addLayout(toolbar)
        return group

    def _build_jobs_group(self) -> QGroupBox:
        group = QGroupBox("任务列表")
        layout = QVBoxLayout(group)

        toolbar = QHBoxLayout()
        self.refresh_jobs_button = QPushButton("刷新")
        self.refresh_jobs_button.clicked.connect(self.refresh_jobs)
        self.start_button = QPushButton("开始")
        self.start_button.clicked.connect(lambda: self.transition_selected_job("running"))
        self.pause_button = QPushButton("暂停")
        self.pause_button.clicked.connect(lambda: self.transition_selected_job("paused"))
        self.resume_button = QPushButton("继续")
        self.resume_button.clicked.connect(lambda: self.transition_selected_job("running"))
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(lambda: self.transition_selected_job("canceled"))
        toolbar.addWidget(self.refresh_jobs_button)
        toolbar.addStretch(1)
        toolbar.addWidget(self.start_button)
        toolbar.addWidget(self.pause_button)
        toolbar.addWidget(self.resume_button)
        toolbar.addWidget(self.cancel_button)
        layout.addLayout(toolbar)

        self.jobs_table = QTableWidget(0, 6)
        self.jobs_table.setHorizontalHeaderLabels(["任务", "状态", "来源数", "同步", "版本", "更新时间"])
        self.jobs_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.jobs_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.jobs_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.jobs_table.verticalHeader().setVisible(False)
        self.jobs_table.itemSelectionChanged.connect(self._refresh_job_buttons)
        layout.addWidget(self.jobs_table)
        return group

    @Slot()
    def choose_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "选择备份文件")
        self.add_sources(files)

    @Slot()
    def choose_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择备份文件夹")
        if directory:
            self.add_sources([directory])

    def add_sources(self, paths: list[str]) -> None:
        changed = False
        existing = {source.local_path.casefold() for source in self._pending_sources}
        for path in paths:
            cleaned = str(Path(path).expanduser())
            if not cleaned:
                continue
            key = cleaned.casefold()
            if key in existing:
                continue
            source_type: BackupSourceType = "directory" if Path(cleaned).is_dir() else "file"
            self._pending_sources.append(PendingSource(local_path=cleaned, source_type=source_type))
            existing.add(key)
            changed = True
        if changed:
            self._render_pending_sources()
            self.status_changed.emit(f"已添加 {len(self._pending_sources)} 个待备份来源。")

    @Slot()
    def remove_selected_pending_source(self) -> None:
        selected = sorted({index.row() for index in self.pending_table.selectedIndexes()}, reverse=True)
        for row in selected:
            if 0 <= row < len(self._pending_sources):
                del self._pending_sources[row]
        self._render_pending_sources()
        self.status_changed.emit(f"当前待建任务来源数：{len(self._pending_sources)}。")

    @Slot()
    def clear_pending_sources(self) -> None:
        self._pending_sources.clear()
        self._render_pending_sources()
        self.status_changed.emit("已清空待建任务来源。")

    @Slot()
    def create_job(self) -> None:
        try:
            created = self._manager.create_job(
                [
                    BackupSourceInput(source.local_path, source.source_type)
                    for source in self._pending_sources
                ],
                job_name=self.job_name_input.text(),
            )
        except BackupJobError as exc:
            self._warn(str(exc))
            return
        self._pending_sources.clear()
        self.job_name_input.clear()
        self._render_pending_sources()
        self.refresh_jobs()
        self._select_job(created.job.backup_job_id)
        self.status_changed.emit(f"已创建任务：{created.job.job_name}，状态 {status_label(created.job.status)}。")

    @Slot()
    def refresh_jobs(self) -> None:
        self._jobs = self._manager.list_jobs()
        self.jobs_table.setRowCount(len(self._jobs))
        for row, item in enumerate(self._jobs):
            job = item.job
            values = [
                job.job_name,
                status_label(job.status),
                str(job.source_count),
                job.sync_status,
                str(job.data_version),
                job.updated_at,
            ]
            for col, value in enumerate(values):
                table_item = QTableWidgetItem(value)
                table_item.setData(Qt.ItemDataRole.UserRole, job.backup_job_id)
                self.jobs_table.setItem(row, col, table_item)
        self._refresh_job_buttons()

    @Slot()
    def transition_selected_job(self, status: str) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            self._warn("请先选择一个任务。")
            return
        try:
            updated = self._manager.transition_job(job_id, status)
        except BackupJobError as exc:
            self._warn(str(exc))
            return
        self.refresh_jobs()
        self._select_job(updated.job.backup_job_id)
        self.status_changed.emit(f"任务 {updated.job.job_name} 已更新为 {status_label(updated.job.status)}。")

    def _render_pending_sources(self) -> None:
        self.pending_table.setRowCount(len(self._pending_sources))
        for row, source in enumerate(self._pending_sources):
            path_hash = _path_hash_for_display(source.local_path)
            values = ["文件夹" if source.source_type == "directory" else "文件", Path(source.local_path).name, path_hash]
            for col, value in enumerate(values):
                self.pending_table.setItem(row, col, QTableWidgetItem(value))

    def _selected_job_id(self) -> str:
        row = self.jobs_table.currentRow()
        if row < 0:
            return ""
        item = self.jobs_table.item(row, 0)
        if item is None:
            return ""
        value = item.data(Qt.ItemDataRole.UserRole)
        return str(value or "")

    def _select_job(self, backup_job_id: str) -> None:
        for row in range(self.jobs_table.rowCount()):
            item = self.jobs_table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == backup_job_id:
                self.jobs_table.selectRow(row)
                return

    def _refresh_job_buttons(self) -> None:
        row = self.jobs_table.currentRow()
        selected = 0 <= row < len(self._jobs)
        status = self._jobs[row].job.status if selected else ""
        self.start_button.setEnabled(selected and status in {"queued", "failed_retryable"})
        self.pause_button.setEnabled(selected and status == "running")
        self.resume_button.setEnabled(selected and status == "paused")
        self.cancel_button.setEnabled(selected and status in {"queued", "running", "paused", "failed_retryable"})

    def _warn(self, message: str) -> None:
        self.status_changed.emit(message)
        QMessageBox.warning(self, "提示", message)


class MainWindow(QMainWindow):
    def __init__(self, config: MainWindowConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        store = SQLiteClientStore(config.sqlite_path)
        store.migrate()
        self._backup_page = BackupTaskPage(BackupJobManager(store, device_id=config.device_id or "current-device"))
        self._baidu_page = BaiduSettingsPage(
            BaiduSettingsPageConfig(
                cloud_api_base_url=config.cloud_api_base_url,
                device_token=config.device_token,
                device_id=config.device_id,
                device_credential_source=config.device_credential_source,
            )
        )
        self._build_ui()

    def _build_ui(self) -> None:
        self.setWindowTitle("百度网盘加密备份")
        shell = QWidget()
        layout = QHBoxLayout(shell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.nav = QListWidget()
        self.nav.setFixedWidth(180)
        self.nav.addItem(QListWidgetItem("备份任务"))
        self.nav.addItem(QListWidgetItem("百度设置"))
        self.nav.currentRowChanged.connect(self._set_current_page)
        layout.addWidget(self.nav)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._backup_page)
        self.stack.addWidget(self._baidu_page)
        layout.addWidget(self.stack, stretch=1)

        self.status_label = QLabel("准备就绪")
        self._backup_page.status_changed.connect(self.status_label.setText)
        self.setCentralWidget(shell)
        self.statusBar().addPermanentWidget(self.status_label, stretch=1)
        self.nav.setCurrentRow(0)
        self.resize(1180, 780)
        self.setStyleSheet(
            """
            QWidget { font-size: 13px; }
            QLabel#pageTitle { font-size: 22px; font-weight: 600; }
            QListWidget { border: 0; border-right: 1px solid #c7cdd4; background: #f5f7fa; padding-top: 10px; }
            QListWidget::item { min-height: 38px; padding-left: 14px; }
            QListWidget::item:selected { background: #d8e8ff; color: #111827; }
            QGroupBox { font-weight: 600; border: 1px solid #c7cdd4; border-radius: 6px; margin-top: 8px; padding-top: 12px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
            QPushButton { min-height: 30px; padding: 4px 10px; }
            QLineEdit { min-height: 28px; }
            QTableWidget { gridline-color: #d7dde3; selection-background-color: #d8e8ff; }
            """
        )

    @Slot(int)
    def _set_current_page(self, row: int) -> None:
        if row >= 0:
            self.stack.setCurrentIndex(row)

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self._baidu_page.close()
        super().closeEvent(event)


def run_main_window_app(settings: ClientSettings | None = None) -> int:
    loaded = settings or ClientSettings.from_env()
    loaded.validate(require_device_token=False)
    credentials, source = resolve_or_register_device_credentials(
        cloud_api_base_url=loaded.cloud_api_base_url,
        provided_device_token=loaded.device_token,
    )
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(
        MainWindowConfig(
            cloud_api_base_url=loaded.cloud_api_base_url,
            device_token=credentials.device_token,
            device_id=credentials.device_id,
            sqlite_path=loaded.local_sqlite_path,
            device_credential_source=source,
        )
    )
    window.show()
    return app.exec()


def _mime_has_local_files(mime: QMimeData) -> bool:
    return bool(_local_paths_from_mime(mime))


def _local_paths_from_mime(mime: QMimeData) -> list[str]:
    paths: list[str] = []
    for url in mime.urls():
        if url.isLocalFile():
            paths.append(url.toLocalFile())
    return paths


def _path_hash_for_display(local_path: str) -> str:
    from auto_backup_client.backup_jobs import path_sha256

    return path_sha256(local_path)[:12]


if __name__ == "__main__":
    raise SystemExit(run_main_window_app())

