from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar

from PySide6.QtCore import QMimeData, QObject, QRunnable, QThreadPool, Qt, Signal, Slot
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
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
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from auto_backup_client.baidu.reconcile import RemoteObjectReconciler, RemoteReconcileReport, RemoteReconcileScope
from auto_backup_client.baidu.reconcile_repair import (
    CONFIRM_REPAIR_TEXT,
    RemoteObjectRepairer,
    RemoteRepairPlan,
    build_remote_repair_plan,
)
from auto_backup_client.baidu.upload import BaiduNetdiskClient
from auto_backup_client.baidu.auth_workflow import BaiduAuthWorkflow
from auto_backup_client.baidu.cloud_api import BaiduCloudClient
from auto_backup_client.backup_history_sync import sync_device_backup_history
from auto_backup_client.backup_jobs import (
    BackupJobError,
    BackupJobManager,
    BackupJobWithSources,
    BackupSourceInput,
    BackupSourceType,
    status_label,
)
from auto_backup_client.backup_pipeline import BackupPipeline, BackupPipelineError, BackupPipelineOptions, BackupPipelineResult
from auto_backup_client.device_credentials import resolve_or_register_device_credentials
from auto_backup_client.settings import ClientSettings
from auto_backup_client.source_mapping import SourceMappingQuery, SourceMappingReport, path_digest, short_digest
from auto_backup_client.source_cleanup import CLEANUP_CONFIRM_TEXT, PERMANENT_DELETE_CONFIRM_TEXT, SourceCleanupService
from auto_backup_client.restore_flow import BaiduArchiveDownloader, RestoreService
from auto_backup_client.sqlite_store import SYNC_ENTITY_TABLES, SQLiteClientStore
from auto_backup_client.ui.baidu_settings import BaiduSettingsPage, BaiduSettingsPageConfig


T = TypeVar("T")


@dataclass(frozen=True)
class MainWindowConfig:
    cloud_api_base_url: str
    device_token: str
    device_id: str
    sqlite_path: str
    cache_root: str
    device_credential_source: str = ""


@dataclass(frozen=True)
class PendingSource:
    local_path: str
    source_type: BackupSourceType


@dataclass(frozen=True)
class BackupTaskPageConfig:
    cloud_api_base_url: str = ""
    device_token: str = ""
    device_id: str = ""
    cache_root: str = ""
    sync_batch_size: int = 100
    max_sync_batches: int = 20


class TaskSignals(QObject, Generic[T]):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()


class TaskWorker(QRunnable, Generic[T]):
    def __init__(self, task: Callable[[], T]) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self._task = task
        self.signals: TaskSignals[T] = TaskSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.succeeded.emit(self._task())
        except Exception as exc:
            self.signals.failed.emit(_safe_ui_error(exc))
        finally:
            self.signals.finished.emit()


class BackupTaskPage(QWidget):
    status_changed = Signal(str)
    backup_finished = Signal(str)
    cache_root_changed = Signal(str)

    def __init__(
        self,
        manager: BackupJobManager,
        config: BackupTaskPageConfig | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager
        self._config = config or BackupTaskPageConfig(device_id=manager.device_id)
        self._thread_pool = QThreadPool.globalInstance()
        self._workers: list[TaskWorker[object]] = []
        self._running_job_id = ""
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
        self.add_sources_button = QPushButton("添加来源")
        self.add_sources_button.clicked.connect(self.choose_sources)
        name_row.addWidget(self.add_sources_button)
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
        toolbar.addWidget(QLabel("可拖拽或添加任意文件/文件夹"))
        toolbar.addStretch(1)
        toolbar.addWidget(self.remove_source_button)
        toolbar.addWidget(self.clear_sources_button)
        toolbar.addWidget(self.create_job_button)
        layout.addLayout(toolbar)
        return group

    def _build_jobs_group(self) -> QGroupBox:
        group = QGroupBox("任务列表")
        layout = QVBoxLayout(group)

        run_form = QFormLayout()
        self.archive_password_input = QLineEdit()
        self.archive_password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.archive_password_input.setPlaceholderText("运行时输入，不保存")
        run_form.addRow("归档密码", self.archive_password_input)
        self.authorization_password_input = QLineEdit()
        self.authorization_password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.authorization_password_input.setPlaceholderText("留空则复用归档密码")
        run_form.addRow("授权密码", self.authorization_password_input)
        self.cache_root_input = QLineEdit(self._config.cache_root)
        self.cache_root_input.setPlaceholderText("留空使用启动配置中的缓存目录")
        self.choose_cache_root_button = QPushButton("选择")
        self.choose_cache_root_button.clicked.connect(self.choose_cache_root)
        cache_row = QHBoxLayout()
        cache_row.addWidget(self.cache_root_input, stretch=1)
        cache_row.addWidget(self.choose_cache_root_button)
        run_form.addRow("缓存目录", cache_row)
        self.cache_budget_checkbox = QCheckBox("执行前检查 40GiB 缓存预算")
        self.cache_budget_checkbox.setChecked(True)
        run_form.addRow("", self.cache_budget_checkbox)
        layout.addLayout(run_form)

        toolbar = QHBoxLayout()
        self.refresh_jobs_button = QPushButton("刷新")
        self.refresh_jobs_button.clicked.connect(self.refresh_jobs)
        self.start_button = QPushButton("开始")
        self.start_button.clicked.connect(self.start_selected_job)
        self.pause_button = QPushButton("暂停")
        self.pause_button.clicked.connect(lambda: self.transition_selected_job("paused"))
        self.resume_button = QPushButton("继续")
        self.resume_button.clicked.connect(self.start_selected_job)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(lambda: self.transition_selected_job("canceled"))
        toolbar.addWidget(self.refresh_jobs_button)
        toolbar.addStretch(1)
        toolbar.addWidget(self.start_button)
        toolbar.addWidget(self.pause_button)
        toolbar.addWidget(self.resume_button)
        toolbar.addWidget(self.cancel_button)
        layout.addLayout(toolbar)

        self.jobs_table = QTableWidget(0, 8)
        self.jobs_table.setHorizontalHeaderLabels(["任务", "状态", "阶段", "说明", "来源数", "同步", "版本", "更新时间"])
        self.jobs_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.jobs_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.jobs_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.jobs_table.verticalHeader().setVisible(False)
        self.jobs_table.itemSelectionChanged.connect(self._refresh_job_buttons)
        layout.addWidget(self.jobs_table)
        return group

    @Slot()
    def choose_cache_root(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择临时缓存目录")
        if directory:
            self.cache_root_input.setText(directory)

    @Slot()
    def choose_sources(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "选择备份文件")
        if files:
            self.add_sources(files)
            return
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
                job.last_stage,
                job.last_error,
                str(job.source_count),
                job.sync_status,
                str(job.data_version),
                job.updated_at,
            ]
            for col, value in enumerate(values):
                table_item = QTableWidgetItem(value)
                table_item.setData(Qt.ItemDataRole.UserRole, job.backup_job_id)
                table_item.setToolTip(value)
                self.jobs_table.setItem(row, col, table_item)
        self._refresh_job_buttons()

    @Slot()
    def start_selected_job(self) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            self._warn("请先选择一个任务。")
            return
        if self._running_job_id:
            self._warn("已有备份任务正在执行。")
            return
        archive_password = self.archive_password_input.text()
        authorization_password = self.authorization_password_input.text() or archive_password
        if not archive_password:
            self._warn("请输入归档密码。")
            return
        cache_root = self.cache_root_input.text().strip() or self._config.cache_root
        if not self._config.cloud_api_base_url or not self._config.device_token or not cache_root:
            self._warn("缺少云端连接、设备凭据或缓存目录配置。")
            return
        self._config = BackupTaskPageConfig(
            cloud_api_base_url=self._config.cloud_api_base_url,
            device_token=self._config.device_token,
            device_id=self._config.device_id,
            cache_root=cache_root,
            sync_batch_size=self._config.sync_batch_size,
            max_sync_batches=self._config.max_sync_batches,
        )
        self.cache_root_changed.emit(cache_root)
        enforce_cache_budget = self.cache_budget_checkbox.isChecked()
        self.archive_password_input.clear()
        self.authorization_password_input.clear()
        self._running_job_id = job_id
        self._refresh_job_buttons()
        self.status_changed.emit("备份已开始：扫描、压缩、上传、同步和远端校对将在后台执行。")
        worker: TaskWorker[BackupPipelineResult] = TaskWorker(
            lambda: self._run_backup_pipeline(
                job_id,
                archive_password=archive_password,
                authorization_password=authorization_password,
                enforce_cache_budget=enforce_cache_budget,
            )
        )
        worker.signals.succeeded.connect(self._handle_pipeline_succeeded)
        worker.signals.failed.connect(self._handle_pipeline_failed)
        worker.signals.finished.connect(lambda: self._pipeline_worker_finished(worker))
        self._workers.append(worker)  # keep QRunnable and signal objects alive.
        self._thread_pool.start(worker)

    def _run_backup_pipeline(
        self,
        backup_job_id: str,
        *,
        archive_password: str,
        authorization_password: str,
        enforce_cache_budget: bool,
    ) -> BackupPipelineResult:
        with BaiduCloudClient(
            self._config.cloud_api_base_url,
            self._config.device_token,
            timeout=30.0,
            device_id=self._config.device_id or self._manager.device_id,
        ) as cloud:
            workflow = BaiduAuthWorkflow(cloud, device_id=self._config.device_id or self._manager.device_id)
            account_id = _selected_account_id_for_ui(workflow)
            decrypted = workflow.decrypt_password_token(account_id, authorization_password=authorization_password)
            actual_account_id = decrypted.encrypted.account_id or account_id
            with BaiduNetdiskClient(decrypted.token.access_token, timeout=120.0) as baidu:
                return BackupPipeline(
                    store=self._manager.store,
                    device_id=self._config.device_id or self._manager.device_id,
                    baidu_client=baidu,
                    cloud_client=cloud,
                ).run_job(
                    backup_job_id,
                    BackupPipelineOptions(
                        cache_root=self._config.cache_root,
                        password=archive_password,
                        account_id=actual_account_id,
                        run_upload=True,
                        sync_outbox=True,
                        reconcile_remote=True,
                        mark_completed=True,
                        sync_batch_size=self._config.sync_batch_size,
                        max_sync_batches=self._config.max_sync_batches,
                        enforce_cache_budget=enforce_cache_budget,
                    ),
                )

    @Slot(object)
    def _handle_pipeline_succeeded(self, result: object) -> None:
        if not isinstance(result, BackupPipelineResult):
            self.status_changed.emit("备份完成，但返回结果无法识别。")
            return
        self.refresh_jobs()
        self._select_job(result.backup_job_id)
        status = "已完成" if result.completed else f"停在阶段 {result.final_stage}"
        uploaded_parts = len(result.upload.uploaded_partseqs) if result.upload is not None else 0
        uploaded_parts = sum(len(upload.uploaded_partseqs) for upload in result.uploads) if result.uploads else uploaded_parts
        archive_count = len(result.archives) if result.archives else 1
        self.status_changed.emit(
            f"备份{status}：文件 {result.scan.file_count}，归档 {archive_count}，上传分片 {uploaded_parts}。"
        )
        self.backup_finished.emit(result.backup_job_id)

    @Slot(str)
    def _handle_pipeline_failed(self, message: str) -> None:
        selected_job_id = self._running_job_id
        self.refresh_jobs()
        if selected_job_id:
            self._select_job(selected_job_id)
        self._warn(f"备份失败：{message}")

    def _pipeline_worker_finished(self, worker: TaskWorker[object]) -> None:
        if worker in self._workers:
            self._workers.remove(worker)
        self._running_job_id = ""
        self._refresh_job_buttons()

    @Slot()
    def transition_selected_job(self, status: str) -> None:
        if self._running_job_id:
            self._warn("备份执行中，暂不支持从 UI 暂停或取消后台任务。")
            return
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
        running = bool(self._running_job_id)
        self.start_button.setEnabled(not running and selected and status in {"queued", "running", "failed_retryable"})
        self.pause_button.setEnabled(not running and selected and status == "running")
        self.resume_button.setEnabled(not running and selected and status == "paused")
        self.cancel_button.setEnabled(not running and selected and status in {"queued", "running", "paused", "failed_retryable"})

    def _warn(self, message: str) -> None:
        self.status_changed.emit(message)
        QMessageBox.warning(self, "提示", message)


class SourceMappingPage(QWidget):
    status_changed = Signal(str)

    def __init__(self, store: SQLiteClientStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._query = SourceMappingQuery(store)
        self._build_ui()
        self.refresh_mapping()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = QLabel("来源映射")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        filters = QHBoxLayout()
        filters.addWidget(QLabel("任务 ID"))
        self.job_id_input = QLineEdit()
        self.job_id_input.setPlaceholderText("留空显示最近记录")
        filters.addWidget(self.job_id_input, stretch=2)
        filters.addWidget(QLabel("关键字"))
        self.keyword_input = QLineEdit()
        self.keyword_input.setPlaceholderText("任务名、文件名、relative path、content/archive hash")
        filters.addWidget(self.keyword_input, stretch=2)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.refresh_mapping)
        filters.addWidget(self.refresh_button)
        layout.addLayout(filters)

        self.summary_label = QLabel("尚未加载")
        layout.addWidget(self.summary_label)

        self.mapping_table = QTableWidget(0, 14)
        self.mapping_table.setHorizontalHeaderLabels(
            [
                "任务",
                "状态",
                "设备",
                "来源",
                "文件名",
                "大小",
                "SHA256",
                "content_id",
                "去重",
                "archive",
                "成员",
                "远端",
                "清理",
                "恢复",
            ]
        )
        self.mapping_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.mapping_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.mapping_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.mapping_table.verticalHeader().setVisible(False)
        layout.addWidget(self.mapping_table, stretch=1)

    @Slot()
    def refresh_mapping(self) -> None:
        try:
            report = self._query.list_rows(
                backup_job_id=self.job_id_input.text(),
                keyword=self.keyword_input.text(),
                limit=500,
            )
        except ValueError as exc:
            self._warn(str(exc))
            return
        self._render_report(report)
        self.status_changed.emit(f"来源映射已刷新：{report.summary.total_rows} 行。")

    def _render_report(self, report: SourceMappingReport) -> None:
        summary = report.summary
        self.summary_label.setText(
            " / ".join(
                [
                    f"映射行 {summary.total_rows}",
                    f"任务 {summary.job_count}",
                    f"来源 {summary.source_count}",
                    f"内容 {summary.content_count}",
                    f"归档 {summary.archive_count}",
                    f"远端对象 {summary.remote_object_count}",
                    f"可恢复候选 {summary.baidu_ready_count}",
                ]
            )
        )
        self.mapping_table.setRowCount(len(report.rows))
        for row_index, row in enumerate(report.rows):
            remote_label = "已确认" if row.baidu_ready else row.remote_archive_status
            values = [
                row.job_name,
                row.job_status,
                short_digest(row.device_id),
                f"{row.source_seq}:{row.source_display_name} {short_digest(row.source_path_sha256)}",
                row.display_name,
                str(row.size_bytes),
                short_digest(row.sha256),
                short_digest(row.content_id),
                row.dedupe_status,
                f"{row.archive_seq or ''} {short_digest(row.archive_sha256)} {row.archive_type}",
                row.archive_member_path,
                f"{remote_label} {short_digest(row.remote_archive_path_sha256)}",
                row.cleanup_status,
                row.restore_status,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, row.backup_job_id)
                self.mapping_table.setItem(row_index, col, item)

    def _warn(self, message: str) -> None:
        self.status_changed.emit(message)
        QMessageBox.warning(self, "提示", message)


class RemoteReconcilePage(QWidget):
    status_changed = Signal(str)

    def __init__(
        self,
        store: SQLiteClientStore,
        *,
        cloud_api_base_url: str,
        device_token: str,
        device_id: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._cloud_api_base_url = cloud_api_base_url
        self._device_token = device_token
        self._device_id = device_id or "current-device"
        self._last_report: RemoteReconcileReport | None = None
        self._last_plan: RemoteRepairPlan | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = QLabel("远端校对")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        scope_group = QGroupBox("校对范围")
        form = QFormLayout(scope_group)
        self.scope_type_combo = QComboBox()
        self.scope_type_combo.addItems(["job_id", "upload_session_id", "remote_dir"])
        self.scope_value_input = QLineEdit()
        self.scope_value_input.setPlaceholderText("输入 job_id、upload_session_id 或百度远端目录")
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("授权密码仅用于本机解密，不保存")
        self.account_id_input = QLineEdit()
        self.account_id_input.setPlaceholderText("留空使用当前设备已选择账号")
        self.recursive_checkbox = QCheckBox("递归 listall")
        self.recursive_checkbox.setChecked(True)
        self.page_limit_spin = QSpinBox()
        self.page_limit_spin.setRange(1, 5000)
        self.page_limit_spin.setValue(1000)
        form.addRow("范围类型", self.scope_type_combo)
        form.addRow("范围值", self.scope_value_input)
        form.addRow("账号 ID", self.account_id_input)
        form.addRow("授权密码", self.password_input)
        form.addRow("列表方式", self.recursive_checkbox)
        form.addRow("分页上限", self.page_limit_spin)
        layout.addWidget(scope_group)

        toolbar = QHBoxLayout()
        self.reconcile_button = QPushButton("执行校对")
        self.reconcile_button.clicked.connect(self.run_reconcile)
        self.apply_button = QPushButton("确认修复")
        self.apply_button.clicked.connect(self.apply_selected_repairs)
        self.confirm_input = QLineEdit()
        self.confirm_input.setPlaceholderText(CONFIRM_REPAIR_TEXT)
        toolbar.addWidget(self.reconcile_button)
        toolbar.addWidget(QLabel("确认短语"))
        toolbar.addWidget(self.confirm_input, stretch=1)
        toolbar.addWidget(self.apply_button)
        layout.addLayout(toolbar)

        self.summary_label = QLabel("尚未校对")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.findings_table = QTableWidget(0, 8)
        self.findings_table.setHorizontalHeaderLabels(["状态", "对象", "建议", "动作", "选择", "写入", "远端路径", "本地对象"])
        self.findings_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.findings_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.findings_table.setWordWrap(True)
        self.findings_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.findings_table.horizontalHeader().setStretchLastSection(True)
        self.findings_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.findings_table, stretch=1)

    @Slot()
    def run_reconcile(self) -> None:
        try:
            scope = self._scope_from_inputs()
            password = self.password_input.text()
            if not password:
                raise ValueError("authorization password is required")
            with BaiduCloudClient(self._cloud_api_base_url, self._device_token, timeout=30.0, device_id=self._device_id) as cloud:
                workflow = BaiduAuthWorkflow(cloud, device_id=self._device_id)
                account_id = self.account_id_input.text().strip() or _selected_account_id_for_ui(workflow)
                decrypted = workflow.decrypt_password_token(account_id, authorization_password=password)
            with BaiduNetdiskClient(decrypted.token.access_token, timeout=120.0) as baidu:
                report = RemoteObjectReconciler(store=self._store, baidu=baidu).reconcile(scope)
            plan = build_remote_repair_plan(report)
        except Exception as exc:
            self._warn(_safe_ui_error(exc))
            return
        self._last_report = report
        self._last_plan = plan
        self._render_plan(plan)
        self.status_changed.emit(f"远端校对完成：{len(report.findings)} 个 finding。")

    @Slot()
    def apply_selected_repairs(self) -> None:
        if self._last_plan is None:
            self._warn("请先执行校对。")
            return
        if self.confirm_input.text().strip() != CONFIRM_REPAIR_TEXT:
            self._warn("确认短语不匹配。")
            return
        try:
            result = RemoteObjectRepairer(
                store=self._store,
                updated_by_device_id=self._device_id,
            ).apply(self._last_plan, dry_run=False)
        except Exception as exc:
            self._warn(_safe_ui_error(exc))
            return
        self.summary_label.setText(
            f"修复完成：候选 {result.candidate_count}，可写 {result.writable_count}，选中 {result.selected_count}，已写入 {result.applied_count}。"
        )
        self.status_changed.emit(f"远端修复已写入 {result.applied_count} 条版本记录。")

    def _scope_from_inputs(self) -> RemoteReconcileScope:
        scope_type = self.scope_type_combo.currentText()
        value = self.scope_value_input.text().strip()
        if scope_type == "job_id":
            return RemoteReconcileScope(job_id=value, recursive=self.recursive_checkbox.isChecked(), page_limit=self.page_limit_spin.value())
        if scope_type == "upload_session_id":
            return RemoteReconcileScope(upload_session_id=value, recursive=self.recursive_checkbox.isChecked(), page_limit=self.page_limit_spin.value())
        return RemoteReconcileScope(remote_dir=value, recursive=self.recursive_checkbox.isChecked(), page_limit=self.page_limit_spin.value())

    def _render_plan(self, plan: RemoteRepairPlan) -> None:
        report = plan.report
        counts = ", ".join(f"{key}={value}" for key, value in sorted(report.status_counts.items()) if value)
        self.summary_label.setText(
            f"对象：本地 SQLite remote_objects/upload_sessions 对比百度网盘 list/listall 返回结果；云端 PostgreSQL 在云端同步页回读。"
            f"范围 {report.scope.scope_type} / 本地 {report.local_object_count} / 百度 {report.remote_object_count} / findings {len(report.findings)} / {counts or '无差异'}"
        )
        self.findings_table.setRowCount(len(plan.candidates))
        for row_index, candidate in enumerate(plan.candidates):
            values = [
                candidate.status,
                candidate.object_type,
                candidate.reason,
                candidate.action,
                "是" if candidate.selected else "否",
                "是" if candidate.will_write else "否",
                short_digest(path_digest(_candidate_remote_path(candidate))),
                short_digest(candidate.local_remote_object_id),
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.findings_table.setItem(row_index, col, item)
        self.findings_table.resizeRowsToContents()

    def _warn(self, message: str) -> None:
        self.status_changed.emit(message)
        QMessageBox.warning(self, "提示", message)


class CloudSyncPage(QWidget):
    status_changed = Signal(str)

    def __init__(
        self,
        store: SQLiteClientStore,
        *,
        cloud_api_base_url: str,
        device_token: str,
        device_id: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._cloud_api_base_url = cloud_api_base_url
        self._device_token = device_token
        self._device_id = device_id or "current-device"
        self._build_ui()
        self.refresh_local_status()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = QLabel("云端同步")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        toolbar = QHBoxLayout()
        self.entity_id_input = QLineEdit()
        self.entity_id_input.setPlaceholderText("输入 entity_id 回读云端摘要")
        self.refresh_button = QPushButton("刷新本地状态")
        self.refresh_button.clicked.connect(self.refresh_local_status)
        self.query_button = QPushButton("查询云端摘要")
        self.query_button.clicked.connect(self.query_cloud_summary)
        toolbar.addWidget(self.entity_id_input, stretch=1)
        toolbar.addWidget(self.refresh_button)
        toolbar.addWidget(self.query_button)
        layout.addLayout(toolbar)

        self.summary_label = QLabel("尚未刷新")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.status_table = QTableWidget(0, 3)
        self.status_table.setHorizontalHeaderLabels(["表", "同步状态", "数量"])
        self.status_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.status_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.status_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.status_table, stretch=1)

        self.recent_table = QTableWidget(0, 5)
        self.recent_table.setHorizontalHeaderLabels(["状态", "类型", "实体", "版本", "错误"])
        self.recent_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.recent_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.recent_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.recent_table.setWordWrap(True)
        self.recent_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.recent_table, stretch=1)

        self.cloud_summary_table = QTableWidget(0, 2)
        self.cloud_summary_table.setHorizontalHeaderLabels(["字段", "值"])
        self.cloud_summary_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.cloud_summary_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.cloud_summary_table, stretch=1)

    @Slot()
    def refresh_local_status(self) -> None:
        try:
            outbox_counts, business_counts, recent = _cloud_sync_local_status(self._store)
        except Exception as exc:
            self._warn(_safe_ui_error(exc))
            return
        pending = outbox_counts.get("pending", 0) + outbox_counts.get("retryable", 0) + outbox_counts.get("syncing", 0)
        self.summary_label.setText(
            f"sync_outbox 总数 {sum(outbox_counts.values())}，待同步/重试 {pending}，冲突 {outbox_counts.get('sync_conflict', 0)}，失败 {outbox_counts.get('failed_terminal', 0)}。"
        )
        rows = [("sync_outbox", status, count) for status, count in sorted(outbox_counts.items())]
        rows.extend((table, status, count) for table, status, count in business_counts)
        self.status_table.setRowCount(len(rows))
        for row_index, (table, status, count) in enumerate(rows):
            for col, value in enumerate((table, status, str(count))):
                self.status_table.setItem(row_index, col, QTableWidgetItem(value))
        self.recent_table.setRowCount(len(recent))
        for row_index, item in enumerate(recent):
            values = [
                str(item["status"]),
                str(item["entity_type"]),
                short_digest(str(item["entity_id"])),
                short_digest(str(item["revision_id"])),
                str(item["last_error"] or ""),
            ]
            for col, value in enumerate(values):
                table_item = QTableWidgetItem(value)
                table_item.setToolTip(str(item["last_error"] or value))
                self.recent_table.setItem(row_index, col, table_item)
        self.status_changed.emit("云端同步本地状态已刷新。")

    @Slot()
    def query_cloud_summary(self) -> None:
        entity_id = self.entity_id_input.text().strip()
        if not entity_id:
            self._warn("请输入 entity_id。")
            return
        try:
            with BaiduCloudClient(
                self._cloud_api_base_url,
                self._device_token,
                timeout=30.0,
                device_id=self._device_id,
            ) as cloud:
                summary = cloud.get_entity_summary(entity_id)
        except Exception as exc:
            self._warn(_safe_ui_error(exc))
            return
        rows = [
            ("entity_id", summary.entity_id),
            ("entity_type", summary.entity_type),
            ("data_version", str(summary.data_version)),
            ("revision_id", short_digest(summary.revision_id)),
            ("canonical_hash", short_digest(summary.canonical_record_sha256)),
            ("updated_by_device_id", short_digest(summary.updated_by_device_id)),
            ("deleted_at", summary.deleted_at.isoformat() if summary.deleted_at else ""),
            ("recent_revisions", str(len(summary.recent_revisions))),
        ]
        for index, revision in enumerate(summary.recent_revisions[:5], start=1):
            rows.append((f"recent_{index}", f"{revision.apply_status} v{revision.data_version} {short_digest(revision.revision_id)}"))
        self.cloud_summary_table.setRowCount(len(rows))
        for row_index, (key, value) in enumerate(rows):
            self.cloud_summary_table.setItem(row_index, 0, QTableWidgetItem(key))
            self.cloud_summary_table.setItem(row_index, 1, QTableWidgetItem(value))
        self.status_changed.emit("云端 summary 已回读。")

    def _warn(self, message: str) -> None:
        self.status_changed.emit(message)
        QMessageBox.warning(self, "提示", message)


class SourceCleanupPage(QWidget):
    status_changed = Signal(str)

    def __init__(self, store: SQLiteClientStore, *, device_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = SourceCleanupService(store, device_id=device_id or "current-device")
        self._candidates = []
        self._build_ui()
        self.refresh_candidates()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = QLabel("原始数据清理")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        filters = QHBoxLayout()
        filters.addWidget(QLabel("任务 ID"))
        self.job_id_input = QLineEdit()
        self.job_id_input.setPlaceholderText("留空显示最近候选")
        filters.addWidget(self.job_id_input, stretch=2)
        filters.addWidget(QLabel("关键字"))
        self.keyword_input = QLineEdit()
        self.keyword_input.setPlaceholderText("任务名、来源名、archive 或远端路径")
        filters.addWidget(self.keyword_input, stretch=2)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.refresh_candidates)
        filters.addWidget(self.refresh_button)
        layout.addLayout(filters)

        method_group = QGroupBox("清理方式")
        form = QFormLayout(method_group)
        self.method_combo = QComboBox()
        self.method_combo.addItem("移入回收站", "recycle_bin")
        self.method_combo.addItem("移动到隔离目录", "quarantine")
        self.advanced_cleanup_checkbox = QCheckBox("高级选项")
        self.advanced_cleanup_checkbox.toggled.connect(self._toggle_permanent_delete_option)
        self.quarantine_input = QLineEdit()
        self.quarantine_input.setPlaceholderText("仅隔离目录方式需要")
        self.choose_quarantine_button = QPushButton("选择")
        self.choose_quarantine_button.clicked.connect(self.choose_quarantine_dir)
        quarantine_row = QHBoxLayout()
        quarantine_row.addWidget(self.quarantine_input, stretch=1)
        quarantine_row.addWidget(self.choose_quarantine_button)
        self.operator_input = QLineEdit("local-user")
        self.confirm_input = QLineEdit()
        self.confirm_input.setPlaceholderText(CLEANUP_CONFIRM_TEXT)
        self.confirm_input.setToolTip(f"执行清理需要输入：{CLEANUP_CONFIRM_TEXT}")
        self.permanent_confirm_input = QLineEdit()
        self.permanent_confirm_input.setPlaceholderText(PERMANENT_DELETE_CONFIRM_TEXT)
        self.permanent_confirm_input.setToolTip(f"永久删除还需要输入：{PERMANENT_DELETE_CONFIRM_TEXT}")
        form.addRow("方式", self.method_combo)
        form.addRow("高级", self.advanced_cleanup_checkbox)
        form.addRow("隔离目录", quarantine_row)
        form.addRow("操作人", self.operator_input)
        form.addRow("清理确认", self.confirm_input)
        form.addRow("永久删除确认", self.permanent_confirm_input)
        layout.addWidget(method_group)

        toolbar = QHBoxLayout()
        self.dry_run_button = QPushButton("预演")
        self.dry_run_button.clicked.connect(lambda: self.apply_cleanup(dry_run=True))
        self.apply_button = QPushButton("执行清理")
        self.apply_button.clicked.connect(lambda: self.apply_cleanup(dry_run=False))
        toolbar.addStretch(1)
        toolbar.addWidget(self.dry_run_button)
        toolbar.addWidget(self.apply_button)
        layout.addLayout(toolbar)

        self.summary_label = QLabel("尚未加载")
        layout.addWidget(self.summary_label)

        self.candidates_table = QTableWidget(0, 10)
        self.candidates_table.setHorizontalHeaderLabels(["状态", "警告", "任务", "文件名", "大小", "SHA256", "路径", "上传", "远端", "原因"])
        self.candidates_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.candidates_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.candidates_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.candidates_table.verticalHeader().setVisible(False)
        layout.addWidget(self.candidates_table, stretch=1)

    @Slot()
    def refresh_candidates(self) -> None:
        try:
            report = self._service.list_candidates(
                backup_job_id=self.job_id_input.text(),
                keyword=self.keyword_input.text(),
                limit=500,
            )
        except Exception as exc:
            self._warn(_safe_ui_error(exc))
            return
        self._candidates = list(report.candidates)
        self.summary_label.setText(
            f"候选 {len(report.candidates)} / 可清理 {report.eligible_count} / 阻塞 {report.blocked_count} / 云端索引待同步提示 {report.sync_pending_count}"
        )
        self.candidates_table.setRowCount(len(self._candidates))
        for row_index, candidate in enumerate(self._candidates):
            values = [
                "可清理" if candidate.eligible else candidate.candidate_status,
                "sync_pending" if candidate.sync_pending_warning else "",
                candidate.job_name,
                candidate.display_name,
                str(candidate.size_bytes),
                short_digest(candidate.sha256),
                short_digest(candidate.path_sha256),
                f"{candidate.upload_status}/{candidate.meta_status}/{candidate.job_index_status}",
                f"{candidate.remote_archive_status}/{candidate.remote_meta_status}/{candidate.remote_job_index_status}",
                candidate.reason,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, candidate.content_reference_id)
                self.candidates_table.setItem(row_index, col, item)
        self.status_changed.emit(f"清理候选已刷新：可清理 {report.eligible_count} 条。")

    @Slot()
    def choose_quarantine_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择隔离目录")
        if directory:
            self.quarantine_input.setText(directory)

    @Slot(bool)
    def _toggle_permanent_delete_option(self, enabled: bool) -> None:
        index = self.method_combo.findData("permanent_delete")
        if enabled and index < 0:
            self.method_combo.addItem("永久删除", "permanent_delete")
            return
        if not enabled and index >= 0:
            if self.method_combo.currentData() == "permanent_delete":
                self.method_combo.setCurrentIndex(0)
            self.method_combo.removeItem(index)

    @Slot()
    def apply_cleanup(self, *, dry_run: bool) -> None:
        selected_ids = tuple(
            str(self.candidates_table.item(row, 0).data(Qt.ItemDataRole.UserRole))
            for row in sorted({index.row() for index in self.candidates_table.selectedIndexes()})
            if self.candidates_table.item(row, 0) is not None
        )
        if not selected_ids:
            self._warn("请先选择要清理的来源。")
            return
        try:
            result = self._service.apply(
                backup_job_id=self.job_id_input.text(),
                content_reference_ids=selected_ids,
                method=self.method_combo.currentData(),
                quarantine_dir=self.quarantine_input.text(),
                cleanup_operator=self.operator_input.text(),
                confirm_text=self.confirm_input.text(),
                permanent_confirm_text=self.permanent_confirm_input.text(),
                dry_run=dry_run,
            )
        except Exception as exc:
            self._warn(_cleanup_ui_error(exc))
            return
        if dry_run:
            self.status_changed.emit(f"清理预演完成：将处理 {result.requested_count} 条。")
            return
        self.status_changed.emit(f"清理完成：成功 {result.applied_count}，失败 {result.failed_count}。")
        self.refresh_candidates()

    def _warn(self, message: str) -> None:
        self.status_changed.emit(message)
        QMessageBox.warning(self, "提示", message)


class RestorePage(QWidget):
    status_changed = Signal(str)

    def __init__(
        self,
        store: SQLiteClientStore,
        *,
        device_id: str,
        cache_root: str,
        cloud_api_base_url: str = "",
        device_token: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._device_id = device_id or "current-device"
        self._cache_root = cache_root
        self._cloud_api_base_url = cloud_api_base_url
        self._device_token = device_token
        self._service = RestoreService(store, device_id=self._device_id, cache_root=cache_root)
        self._candidates = []
        self._build_ui()
        self.refresh_candidates()

    def set_cache_root(self, cache_root: str) -> None:
        cleaned = cache_root.strip()
        if not cleaned or cleaned == self._cache_root:
            return
        self._cache_root = cleaned
        self._service = RestoreService(self._store, device_id=self._device_id, cache_root=cleaned)
        self.refresh_candidates()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = QLabel("恢复")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        filters = QHBoxLayout()
        filters.addWidget(QLabel("任务 ID"))
        self.job_id_input = QLineEdit()
        self.job_id_input.setPlaceholderText("留空显示最近候选")
        filters.addWidget(self.job_id_input, stretch=2)
        filters.addWidget(QLabel("关键字"))
        self.keyword_input = QLineEdit()
        self.keyword_input.setPlaceholderText("任务名、文件名、relative path、content hash")
        filters.addWidget(self.keyword_input, stretch=2)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.refresh_candidates)
        filters.addWidget(self.refresh_button)
        layout.addLayout(filters)

        target_group = QGroupBox("恢复目标")
        form = QFormLayout(target_group)
        self.target_mode_combo = QComboBox()
        self.target_mode_combo.addItem("恢复到手动路径", "manual_path")
        self.target_mode_combo.addItem("恢复到原路径", "original_path")
        self.target_root_input = QLineEdit()
        self.target_root_input.setPlaceholderText("手动路径模式需要")
        self.choose_target_button = QPushButton("选择")
        self.choose_target_button.clicked.connect(self.choose_target_root)
        target_row = QHBoxLayout()
        target_row.addWidget(self.target_root_input, stretch=1)
        target_row.addWidget(self.choose_target_button)
        self.conflict_combo = QComboBox()
        self.conflict_combo.addItem("保留两者", "keep_both")
        self.conflict_combo.addItem("跳过已有文件", "skip_existing")
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("归档密码仅用于本次解压")
        form.addRow("目标模式", self.target_mode_combo)
        form.addRow("目标目录", target_row)
        form.addRow("冲突策略", self.conflict_combo)
        form.addRow("归档密码", self.password_input)
        layout.addWidget(target_group)

        remote_group = QGroupBox("远端拉取")
        remote_form = QFormLayout(remote_group)
        self.account_id_input = QLineEdit()
        self.account_id_input.setPlaceholderText("留空使用当前设备已选择账号")
        self.authorization_password_input = QLineEdit()
        self.authorization_password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.authorization_password_input.setPlaceholderText("授权密码仅用于本机解密，不保存")
        remote_form.addRow("账号 ID", self.account_id_input)
        remote_form.addRow("授权密码", self.authorization_password_input)
        layout.addWidget(remote_group)

        toolbar = QHBoxLayout()
        self.restore_button = QPushButton("执行恢复")
        self.restore_button.clicked.connect(self.apply_restore)
        toolbar.addStretch(1)
        toolbar.addWidget(self.restore_button)
        layout.addLayout(toolbar)

        self.summary_label = QLabel("尚未加载")
        layout.addWidget(self.summary_label)

        self.candidates_table = QTableWidget(0, 11)
        self.candidates_table.setHorizontalHeaderLabels(["状态", "类型", "来源", "任务", "文件数", "总大小", "清理", "恢复", "archive", "远端", "原因"])
        self.candidates_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.candidates_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.candidates_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.candidates_table.verticalHeader().setVisible(False)
        layout.addWidget(self.candidates_table, stretch=1)

    @Slot()
    def refresh_candidates(self) -> None:
        try:
            self._sync_remote_history()
            report = self._service.list_candidates(
                backup_job_id=self.job_id_input.text(),
                keyword=self.keyword_input.text(),
                limit=500,
            )
        except Exception as exc:
            self._warn(_safe_ui_error(exc))
            return
        self._candidates = list(report.candidates)
        self.summary_label.setText(
            f"候选 {len(report.candidates)} / 可恢复 {report.restorable_count} / 本地可用 {report.local_ready_count} / 需下载 {report.needs_download_count} / 阻塞 {report.blocked_count}"
        )
        self.candidates_table.setRowCount(len(self._candidates))
        for row_index, candidate in enumerate(self._candidates):
            values = [
                candidate.candidate_status,
                "文件夹" if candidate.source_type == "directory" else "文件",
                candidate.source_display_name or candidate.display_name,
                candidate.job_name,
                str(candidate.file_count),
                str(candidate.size_bytes),
                candidate.cleanup_status,
                candidate.restore_status,
                short_digest(candidate.archive_sha256),
                candidate.remote_archive_status or "-",
                candidate.reason,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, candidate.restore_candidate_id)
                item.setToolTip(value)
                self.candidates_table.setItem(row_index, col, item)
        self.status_changed.emit(f"恢复候选已刷新：可恢复 {report.restorable_count} 条。")

    def _sync_remote_history(self) -> None:
        if not self._cloud_api_base_url or not self._device_token:
            return
        try:
            with BaiduCloudClient(self._cloud_api_base_url, self._device_token, timeout=30.0, device_id=self._device_id) as cloud:
                sync_device_backup_history(store=self._store, cloud=cloud, limit=5000)
        except Exception:
            return

    @Slot()
    def choose_target_root(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择恢复目标目录")
        if directory:
            self.target_root_input.setText(directory)

    @Slot()
    def apply_restore(self) -> None:
        selected_ids = tuple(
            str(self.candidates_table.item(row, 0).data(Qt.ItemDataRole.UserRole))
            for row in sorted({index.row() for index in self.candidates_table.selectedIndexes()})
            if self.candidates_table.item(row, 0) is not None
        )
        try:
            if self._selected_candidates_need_download(selected_ids):
                if not self._cloud_api_base_url or not self._device_token:
                    raise ValueError("cloud api base url and device token are required for remote archive download")
                authorization_password = self.authorization_password_input.text()
                if not authorization_password:
                    raise ValueError("authorization password is required for remote archive download")
                with BaiduCloudClient(self._cloud_api_base_url, self._device_token, timeout=30.0, device_id=self._device_id) as cloud:
                    workflow = BaiduAuthWorkflow(cloud, device_id=self._device_id)
                    account_id = self.account_id_input.text().strip() or _selected_account_id_for_ui(workflow)
                    decrypted = workflow.decrypt_password_token(account_id, authorization_password=authorization_password)
                with BaiduNetdiskClient(decrypted.token.access_token, timeout=120.0) as baidu:
                    service = RestoreService(
                        self._store,
                        device_id=self._device_id,
                        cache_root=self._cache_root,
                        downloader=BaiduArchiveDownloader(baidu),
                    )
                    result = self._restore_with_service(service, selected_ids)
            else:
                result = self._restore_with_service(self._service, selected_ids)
        except Exception as exc:
            self._warn(_safe_ui_error(exc))
            return
        self.status_changed.emit(
            f"恢复完成：成功 {result.restored_count}，跳过 {result.skipped_count}，失败 {result.failed_count}。"
        )
        self.refresh_candidates()

    def _restore_with_service(self, service: RestoreService, selected_ids: tuple[str, ...]):
        return service.restore(
            backup_job_id=self.job_id_input.text(),
            content_reference_ids=selected_ids,
            target_mode=self.target_mode_combo.currentData(),
            target_root=self.target_root_input.text(),
            password=self.password_input.text(),
            conflict_strategy=self.conflict_combo.currentData(),
        )

    def _selected_candidates_need_download(self, selected_ids: tuple[str, ...]) -> bool:
        selected = set(selected_ids)
        candidates = self._candidates if not selected else [candidate for candidate in self._candidates if candidate.restore_candidate_id in selected]
        return any(candidate.remote_download_available for candidate in candidates)

    def _warn(self, message: str) -> None:
        self.status_changed.emit(message)
        QMessageBox.warning(self, "提示", message)


class MainWindow(QMainWindow):
    def __init__(self, config: MainWindowConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._store = SQLiteClientStore(config.sqlite_path)
        self._store.migrate()
        self._backup_page = BackupTaskPage(
            BackupJobManager(self._store, device_id=config.device_id or "current-device"),
            BackupTaskPageConfig(
                cloud_api_base_url=config.cloud_api_base_url,
                device_token=config.device_token,
                device_id=config.device_id or "current-device",
                cache_root=config.cache_root,
            ),
        )
        self._baidu_page = BaiduSettingsPage(
            BaiduSettingsPageConfig(
                cloud_api_base_url=config.cloud_api_base_url,
                device_token=config.device_token,
                device_id=config.device_id,
                device_credential_source=config.device_credential_source,
            )
        )
        self._source_mapping_page = SourceMappingPage(self._store)
        self._cloud_sync_page = CloudSyncPage(
            self._store,
            cloud_api_base_url=config.cloud_api_base_url,
            device_token=config.device_token,
            device_id=config.device_id,
        )
        self._reconcile_page = RemoteReconcilePage(
            self._store,
            cloud_api_base_url=config.cloud_api_base_url,
            device_token=config.device_token,
            device_id=config.device_id,
        )
        self._cleanup_page = SourceCleanupPage(self._store, device_id=config.device_id)
        self._restore_page = RestorePage(
            self._store,
            device_id=config.device_id,
            cache_root=config.cache_root,
            cloud_api_base_url=config.cloud_api_base_url,
            device_token=config.device_token,
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
        self.nav.addItem(QListWidgetItem("来源映射"))
        self.nav.addItem(QListWidgetItem("云端同步"))
        self.nav.addItem(QListWidgetItem("远端校对"))
        self.nav.addItem(QListWidgetItem("原始数据清理"))
        self.nav.addItem(QListWidgetItem("恢复"))
        self.nav.currentRowChanged.connect(self._set_current_page)
        layout.addWidget(self.nav)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._backup_page)
        self.stack.addWidget(self._baidu_page)
        self.stack.addWidget(self._source_mapping_page)
        self.stack.addWidget(self._cloud_sync_page)
        self.stack.addWidget(self._reconcile_page)
        self.stack.addWidget(self._cleanup_page)
        self.stack.addWidget(self._restore_page)
        layout.addWidget(self.stack, stretch=1)

        self.status_label = QLabel("准备就绪")
        self._backup_page.status_changed.connect(self.status_label.setText)
        self._backup_page.backup_finished.connect(self._refresh_after_backup_finished)
        self._backup_page.cache_root_changed.connect(self._restore_page.set_cache_root)
        self._source_mapping_page.status_changed.connect(self.status_label.setText)
        self._cloud_sync_page.status_changed.connect(self.status_label.setText)
        self._reconcile_page.status_changed.connect(self.status_label.setText)
        self._cleanup_page.status_changed.connect(self.status_label.setText)
        self._restore_page.status_changed.connect(self.status_label.setText)
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

    @Slot(str)
    def _refresh_after_backup_finished(self, backup_job_id: str) -> None:
        self._source_mapping_page.job_id_input.setText(backup_job_id)
        self._source_mapping_page.refresh_mapping()
        self._cloud_sync_page.refresh_local_status()
        self._cleanup_page.job_id_input.setText(backup_job_id)
        self._cleanup_page.refresh_candidates()
        self._restore_page.job_id_input.setText(backup_job_id)
        self._restore_page.refresh_candidates()

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
            cache_root=loaded.local_cache_dir,
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


def _selected_account_id_for_ui(workflow: BaiduAuthWorkflow) -> str:
    selected = [account for account in workflow.load_accounts() if account.selected]
    if not selected:
        raise ValueError("account_id is required because current device has no selected Baidu account")
    return selected[0].account_id


def _safe_ui_error(exc: Exception) -> str:
    if isinstance(exc, BackupPipelineError):
        text = str(exc)
    elif isinstance(exc, ValueError):
        text = str(exc)
    else:
        text = type(exc).__name__
    if len(text) > 180:
        text = text[:177] + "..."
    sanitized = text.replace("\n", " ").replace("\r", " ")
    if "\\" in sanitized or ":/" in sanitized:
        return type(exc).__name__
    return sanitized


def _cleanup_ui_error(exc: Exception) -> str:
    text = str(exc)
    if "cleanup confirmation phrase is required" in text:
        return f"确认短语应为 {CLEANUP_CONFIRM_TEXT}"
    if "permanent delete confirmation phrase is required" in text:
        return f"永久删除确认短语应为 {PERMANENT_DELETE_CONFIRM_TEXT}"
    return _safe_ui_error(exc)


def _cloud_sync_local_status(store: SQLiteClientStore) -> tuple[dict[str, int], list[tuple[str, str, int]], list[dict[str, object]]]:
    with store.connect() as conn:
        outbox_rows = conn.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM sync_outbox
            GROUP BY status
            ORDER BY status
            """
        ).fetchall()
        outbox_counts = {str(row["status"]): int(row["count"]) for row in outbox_rows}
        business_counts: list[tuple[str, str, int]] = []
        for table in sorted(SYNC_ENTITY_TABLES.values()):
            rows = conn.execute(
                f"""
                SELECT sync_status, COUNT(*) AS count
                FROM {table}
                GROUP BY sync_status
                ORDER BY sync_status
                """
            ).fetchall()
            business_counts.extend((table, str(row["sync_status"]), int(row["count"])) for row in rows)
        recent = conn.execute(
            """
            SELECT status, entity_type, entity_id, revision_id, last_error, updated_at
            FROM sync_outbox
            WHERE status IN ('retryable', 'sync_conflict', 'failed_terminal')
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 20
            """
        ).fetchall()
    return outbox_counts, business_counts, [dict(row) for row in recent]


def _candidate_remote_path(candidate) -> str:  # type: ignore[no-untyped-def]
    return str(getattr(candidate, "remote_path", ""))


if __name__ == "__main__":
    raise SystemExit(run_main_window_app())
