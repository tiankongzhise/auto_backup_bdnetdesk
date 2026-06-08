from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QMimeData, Qt, Signal, Slot
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
from auto_backup_client.source_mapping import SourceMappingQuery, SourceMappingReport, path_digest, short_digest
from auto_backup_client.source_cleanup import CLEANUP_CONFIRM_TEXT, PERMANENT_DELETE_CONFIRM_TEXT, SourceCleanupService
from auto_backup_client.restore_flow import RestoreService
from auto_backup_client.sqlite_store import SQLiteClientStore
from auto_backup_client.ui.baidu_settings import BaiduSettingsPage, BaiduSettingsPageConfig


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
        layout.addWidget(self.summary_label)

        self.findings_table = QTableWidget(0, 8)
        self.findings_table.setHorizontalHeaderLabels(["状态", "对象", "建议", "动作", "选择", "写入", "远端路径", "本地对象"])
        self.findings_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.findings_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.findings_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.findings_table.verticalHeader().setVisible(False)
        layout.addWidget(self.findings_table, stretch=1)

    @Slot()
    def run_reconcile(self) -> None:
        try:
            scope = self._scope_from_inputs()
            password = self.password_input.text()
            if not password:
                raise ValueError("authorization password is required")
            with BaiduCloudClient(self._cloud_api_base_url, self._device_token, timeout=30.0) as cloud:
                workflow = BaiduAuthWorkflow(cloud)
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
                self.findings_table.setItem(row_index, col, QTableWidgetItem(value))

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
        self.keyword_input.setPlaceholderText("任务名、文件名、relative path、content hash")
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
        self.method_combo.addItem("永久删除", "permanent_delete")
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
        self.permanent_confirm_input = QLineEdit()
        self.permanent_confirm_input.setPlaceholderText(PERMANENT_DELETE_CONFIRM_TEXT)
        form.addRow("方式", self.method_combo)
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

    @Slot()
    def apply_cleanup(self, *, dry_run: bool) -> None:
        selected_ids = tuple(
            str(self.candidates_table.item(row, 0).data(Qt.ItemDataRole.UserRole))
            for row in sorted({index.row() for index in self.candidates_table.selectedIndexes()})
            if self.candidates_table.item(row, 0) is not None
        )
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
            self._warn(_safe_ui_error(exc))
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

    def __init__(self, store: SQLiteClientStore, *, device_id: str, cache_root: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = RestoreService(store, device_id=device_id or "current-device", cache_root=cache_root)
        self._candidates = []
        self._build_ui()
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

        toolbar = QHBoxLayout()
        self.restore_button = QPushButton("执行恢复")
        self.restore_button.clicked.connect(self.apply_restore)
        toolbar.addStretch(1)
        toolbar.addWidget(self.restore_button)
        layout.addLayout(toolbar)

        self.summary_label = QLabel("尚未加载")
        layout.addWidget(self.summary_label)

        self.candidates_table = QTableWidget(0, 10)
        self.candidates_table.setHorizontalHeaderLabels(["状态", "任务", "文件名", "大小", "SHA256", "路径", "清理", "恢复", "archive", "原因"])
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
            f"候选 {len(report.candidates)} / 可恢复 {report.restorable_count} / 本地可用 {report.local_ready_count} / 需下载 {report.needs_download_count} / 阻塞 {report.blocked_count}"
        )
        self.candidates_table.setRowCount(len(self._candidates))
        for row_index, candidate in enumerate(self._candidates):
            values = [
                candidate.candidate_status,
                candidate.job_name,
                candidate.display_name,
                str(candidate.size_bytes),
                short_digest(candidate.sha256),
                short_digest(candidate.path_sha256),
                candidate.cleanup_status,
                candidate.restore_status,
                short_digest(candidate.archive_sha256),
                candidate.reason,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, candidate.content_reference_id)
                self.candidates_table.setItem(row_index, col, item)
        self.status_changed.emit(f"恢复候选已刷新：可恢复 {report.restorable_count} 条。")

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
            result = self._service.restore(
                backup_job_id=self.job_id_input.text(),
                content_reference_ids=selected_ids,
                target_mode=self.target_mode_combo.currentData(),
                target_root=self.target_root_input.text(),
                password=self.password_input.text(),
                conflict_strategy=self.conflict_combo.currentData(),
            )
        except Exception as exc:
            self._warn(_safe_ui_error(exc))
            return
        self.status_changed.emit(
            f"恢复完成：成功 {result.restored_count}，跳过 {result.skipped_count}，失败 {result.failed_count}。"
        )
        self.refresh_candidates()

    def _warn(self, message: str) -> None:
        self.status_changed.emit(message)
        QMessageBox.warning(self, "提示", message)


class MainWindow(QMainWindow):
    def __init__(self, config: MainWindowConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._store = SQLiteClientStore(config.sqlite_path)
        self._store.migrate()
        self._backup_page = BackupTaskPage(BackupJobManager(self._store, device_id=config.device_id or "current-device"))
        self._baidu_page = BaiduSettingsPage(
            BaiduSettingsPageConfig(
                cloud_api_base_url=config.cloud_api_base_url,
                device_token=config.device_token,
                device_id=config.device_id,
                device_credential_source=config.device_credential_source,
            )
        )
        self._source_mapping_page = SourceMappingPage(self._store)
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
        self.nav.addItem(QListWidgetItem("远端校对"))
        self.nav.addItem(QListWidgetItem("原始数据清理"))
        self.nav.addItem(QListWidgetItem("恢复"))
        self.nav.currentRowChanged.connect(self._set_current_page)
        layout.addWidget(self.nav)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._backup_page)
        self.stack.addWidget(self._baidu_page)
        self.stack.addWidget(self._source_mapping_page)
        self.stack.addWidget(self._reconcile_page)
        self.stack.addWidget(self._cleanup_page)
        self.stack.addWidget(self._restore_page)
        layout.addWidget(self.stack, stretch=1)

        self.status_label = QLabel("准备就绪")
        self._backup_page.status_changed.connect(self.status_label.setText)
        self._source_mapping_page.status_changed.connect(self.status_label.setText)
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
    text = str(exc)
    if len(text) > 180:
        text = text[:177] + "..."
    return text.replace("\n", " ").replace("\r", " ")


def _candidate_remote_path(candidate) -> str:  # type: ignore[no-untyped-def]
    return str(getattr(candidate, "remote_path", ""))


if __name__ == "__main__":
    raise SystemExit(run_main_window_app())
