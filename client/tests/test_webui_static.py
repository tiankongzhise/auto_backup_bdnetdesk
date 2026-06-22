from __future__ import annotations

from pathlib import Path


WEBUI = Path(__file__).resolve().parents[1] / "src" / "auto_backup_client" / "webui"


def test_webui_required_static_files_exist() -> None:
    expected = [
        "index.html",
        "styles.css",
        "js/api.js",
        "js/state.js",
        "js/app.js",
        "js/render.js",
        "js/views/dashboard.js",
        "js/views/jobs.js",
        "js/views/baidu.js",
        "js/views/restore.js",
        "js/views/cleanup.js",
        "js/views/reconcile.js",
        "js/views/settings.js",
    ]

    missing = [item for item in expected if not (WEBUI / item).is_file()]

    assert missing == []


def test_only_api_module_accesses_pywebview_bridge() -> None:
    offenders = []
    for path in (WEBUI / "js").rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        if "window.pywebview" in text and path.name != "api.js":
            offenders.append(path.relative_to(WEBUI).as_posix())

    assert offenders == []


def test_webui_does_not_persist_sensitive_state_in_browser_storage() -> None:
    forbidden = ("localStorage", "sessionStorage", "indexedDB")
    offenders = []
    for path in WEBUI.rglob("*"):
        if path.is_file() and path.suffix in {".html", ".js", ".css"}:
            text = path.read_text(encoding="utf-8")
            if any(term in text for term in forbidden):
                offenders.append(path.relative_to(WEBUI).as_posix())

    assert offenders == []


def test_webui_does_not_use_inner_html_sink() -> None:
    offenders = []
    for path in WEBUI.rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        if "innerHTML" in text:
            offenders.append(path.relative_to(WEBUI).as_posix())

    assert offenders == []


def test_jobs_page_exposes_single_source_picker() -> None:
    text = (WEBUI / "js" / "views" / "jobs.js").read_text(encoding="utf-8")

    assert 'button("添加来源"' in text
    assert 'class: "source-picker"' in text
    assert 'context.call("choose_sources", "mixed")' not in text
    assert 'chooseNativeSources("file")' in text
    assert 'chooseNativeSources("directory")' in text


def test_jobs_page_passes_baidu_upload_parameters_to_bridge() -> None:
    text = (WEBUI / "js" / "views" / "jobs.js").read_text(encoding="utf-8")

    assert "root_dir: rootDir.value.trim()" in text
    assert "part_size: Number(partSize.value)" in text
    assert "max_archive_size_bytes: Number(maxArchiveSize.value)" in text
    assert "check_quota: checkQuota.checked" in text
    assert "sync_outbox: syncOutbox.checked" in text
    assert "reconcile_remote: reconcileRemote.checked" in text
    assert "cleanup_cache_artifacts: cleanupCache.checked" in text


def test_jobs_page_splits_local_and_global_tasks_and_uses_bridge_permissions() -> None:
    text = (WEBUI / "js" / "views" / "jobs.js").read_text(encoding="utf-8")

    assert '"本机任务"' in text
    assert '"全局任务"' in text
    assert "job.current_device" in text
    assert "job.can_continue" in text
    assert "job.can_pause" in text
    assert "job.can_cancel" in text
    assert "owner_device_hint" in text
    assert "window.prompt" not in text


def test_dashboard_recent_jobs_show_scope_and_device() -> None:
    text = (WEBUI / "js" / "views" / "dashboard.js").read_text(encoding="utf-8")

    assert "job.scope_label" in text
    assert "job.owner_device_hint" in text
    assert "job.can_continue" in text


def test_operation_view_surfaces_context_and_error_reason() -> None:
    text = (WEBUI / "js" / "render.js").read_text(encoding="utf-8")

    assert "operation.kind_label" in text
    assert "context.target_label" in text
    assert "context.job_name" in text
    assert "失败原因" in text
    assert "operation.operation_id_hint" in text


def test_backup_page_refreshes_visible_operation_after_continue() -> None:
    text = (WEBUI / "js" / "views" / "jobs.js").read_text(encoding="utf-8")

    assert "statusBox" in text
    assert "redrawOperationStatus(statusBox)" in text
    assert "context.pollOperation(data.operation.operation_id, () => redrawOperationStatus(statusBox))" in text
    assert "已提交继续任务" in text


def test_restore_cleanup_reconcile_pages_expose_task_filters() -> None:
    restore = (WEBUI / "js" / "views" / "restore.js").read_text(encoding="utf-8")
    cleanup = (WEBUI / "js" / "views" / "cleanup.js").read_text(encoding="utf-8")
    reconcile = (WEBUI / "js" / "views" / "reconcile.js").read_text(encoding="utf-8")

    assert 'context.call("list_job_choices")' in restore
    assert 'context.call("list_restore_candidates", { job_id: jobId || "", keyword: keyword || "", limit: 200 })' in restore
    assert '"文件数"' in restore
    assert '"阻塞原因"' in restore
    assert 'context.call("list_job_choices")' in cleanup
    assert 'context.call("list_cleanup_candidates", { job_id: jobId || "", keyword: keyword || "", limit: 200 })' in cleanup
    assert '"待同步"' in cleanup
    assert '"阻塞原因"' in cleanup
    assert 'context.call("list_job_choices")' in reconcile
    assert 'loadMappings(context, jobSelect.value)' in reconcile
    assert "entityId.value = job.entity_id" in reconcile


def test_baidu_page_exposes_device_credential_status() -> None:
    text = (WEBUI / "js" / "views" / "baidu.js").read_text(encoding="utf-8")

    assert "device_token_available" in text
    assert "device_credential_source" in text
    assert "device_credential_error" in text


def test_baidu_page_exposes_visible_authorization_password_verification() -> None:
    text = (WEBUI / "js" / "views" / "baidu.js").read_text(encoding="utf-8")

    assert "验证授权密码" in text
    assert "verify-authorization-password" in text
    assert "verify_baidu_token" in text
    assert "window.prompt" not in text


def test_cleanup_page_gates_permanent_delete_behind_advanced_flag() -> None:
    text = (WEBUI / "js" / "views" / "cleanup.js").read_text(encoding="utf-8")

    assert 'advanced_enabled: advanced.checked' in text
    assert 'advanced.checked && permanentDelete.checked ? "permanent_delete"' in text
    assert '{ value: "permanent_delete"' not in text
