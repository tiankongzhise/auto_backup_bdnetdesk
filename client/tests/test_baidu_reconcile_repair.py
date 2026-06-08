from __future__ import annotations

from auto_backup_client.baidu.reconcile import (
    STATUS_BAIDU_ONLY,
    STATUS_CONSISTENT,
    STATUS_DB_EXISTS_REMOTE_MISSING,
    STATUS_FS_ID_CHANGED,
    STATUS_REMOTE_META_MISMATCH,
    STATUS_REMOTE_META_MISSING,
    STATUS_REMOTE_SIZE_MISMATCH,
    STATUS_REMOTE_UNREADABLE,
    RemoteReconcileFinding,
    RemoteReconcileReport,
    RemoteReconcileScope,
)
from auto_backup_client.baidu.reconcile_repair import (
    ACTION_ACCEPT_BAIDU_METADATA,
    ACTION_MANUAL_REBUILD_FROM_BAIDU,
    ACTION_MARK_REMOTE_MISSING,
    ACTION_NO_ACTION,
    ACTION_RETRY_ACCESS_CHECK,
    RemoteObjectRepairer,
    build_remote_repair_plan,
)
from auto_backup_client.sqlite_store import SQLiteClientStore, build_version_fields


NOW = "2026-06-08T00:00:00Z"
REMOTE_PATH = "/apps/auto_backup_bdnetdesk/backups/2026/06/08/device-secret/job-secret/archives/archive.7z"


def test_repair_plan_maps_reconcile_statuses_to_candidates() -> None:
    report = RemoteReconcileReport(
        scope=RemoteReconcileScope(job_id="job-secret"),
        local_object_count=6,
        remote_object_count=4,
        findings=(
            _finding(STATUS_CONSISTENT, "remote-consistent"),
            _finding(STATUS_DB_EXISTS_REMOTE_MISSING, "remote-missing"),
            _finding(STATUS_REMOTE_META_MISSING, "remote-meta-missing", object_type="archive_meta"),
            _finding(STATUS_FS_ID_CHANGED, "remote-fs", remote_fs_id=222),
            _finding(STATUS_REMOTE_SIZE_MISMATCH, "remote-size", remote_size=99),
            _finding(STATUS_REMOTE_META_MISMATCH, "remote-md5", remote_md5="f" * 32),
            _finding(STATUS_BAIDU_ONLY, ""),
            _finding(STATUS_REMOTE_UNREADABLE, "remote-unreadable"),
        ),
    )

    plan = build_remote_repair_plan(report)
    actions_by_status = {candidate.status: candidate.action for candidate in plan.candidates}

    assert actions_by_status[STATUS_CONSISTENT] == ACTION_NO_ACTION
    assert actions_by_status[STATUS_DB_EXISTS_REMOTE_MISSING] == ACTION_MARK_REMOTE_MISSING
    assert actions_by_status[STATUS_REMOTE_META_MISSING] == ACTION_MARK_REMOTE_MISSING
    assert actions_by_status[STATUS_FS_ID_CHANGED] == ACTION_ACCEPT_BAIDU_METADATA
    assert actions_by_status[STATUS_REMOTE_SIZE_MISMATCH] == ACTION_ACCEPT_BAIDU_METADATA
    assert actions_by_status[STATUS_REMOTE_META_MISMATCH] == ACTION_ACCEPT_BAIDU_METADATA
    assert actions_by_status[STATUS_BAIDU_ONLY] == ACTION_MANUAL_REBUILD_FROM_BAIDU
    assert actions_by_status[STATUS_REMOTE_UNREADABLE] == ACTION_RETRY_ACCESS_CHECK
    assert plan.writable_count == 5
    assert len(plan.selected_writable_candidates) == 5


def test_repair_dry_run_does_not_write_sqlite_or_outbox(tmp_path) -> None:
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    _insert_remote_object(store)

    plan = build_remote_repair_plan(_report_with(_finding(STATUS_DB_EXISTS_REMOTE_MISSING, "remote-archive")))
    result = RemoteObjectRepairer(store=store, updated_by_device_id="repair-device").apply(plan, dry_run=True, now=NOW)

    with store.connect() as conn:
        remote = conn.execute("SELECT status, data_version, updated_by_device_id FROM remote_objects").fetchone()
        outbox_count = conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0]

    assert result.applied_count == 0
    assert remote["status"] == "remote_created"
    assert remote["data_version"] == 1
    assert remote["updated_by_device_id"] == "device-secret"
    assert outbox_count == 1


def test_repair_confirmed_writes_versioned_remote_object_and_outbox(tmp_path) -> None:
    store = SQLiteClientStore(tmp_path / "backup_state.sqlite3")
    store.migrate()
    _insert_remote_object(store)

    plan = build_remote_repair_plan(
        _report_with(
            _finding(
                STATUS_REMOTE_SIZE_MISMATCH,
                "remote-archive",
                remote_size=20,
                remote_md5="c" * 32,
                remote_fs_id=456,
            )
        )
    )
    result = RemoteObjectRepairer(store=store, updated_by_device_id="repair-device").apply(plan, dry_run=False, now=NOW)

    with store.connect() as conn:
        remote = conn.execute("SELECT status, size_bytes, md5, fs_id, data_version, updated_by_device_id, revision_id FROM remote_objects").fetchone()
        outbox = conn.execute("SELECT entity_type, revision_id, status, payload_json FROM sync_outbox ORDER BY created_at, rowid").fetchall()

    assert result.applied_count == 1
    assert remote["status"] == "remote_created"
    assert remote["size_bytes"] == 20
    assert remote["md5"] == "c" * 32
    assert remote["fs_id"] == 456
    assert remote["data_version"] == 2
    assert remote["updated_by_device_id"] == "repair-device"
    assert len(outbox) == 2
    assert outbox[-1]["entity_type"] == "remote_objects"
    assert outbox[-1]["revision_id"] == remote["revision_id"]
    assert outbox[-1]["status"] == "pending"
    assert '"size_bytes":20' in outbox[-1]["payload_json"]


def test_repair_action_filter_can_select_only_missing_marker() -> None:
    plan = build_remote_repair_plan(
        _report_with(
            _finding(STATUS_DB_EXISTS_REMOTE_MISSING, "remote-missing"),
            _finding(STATUS_REMOTE_SIZE_MISMATCH, "remote-size", remote_size=20),
        ),
        action_filter=(ACTION_MARK_REMOTE_MISSING,),
    )

    assert [candidate.action for candidate in plan.selected_writable_candidates] == [ACTION_MARK_REMOTE_MISSING]


def _report_with(*findings: RemoteReconcileFinding) -> RemoteReconcileReport:
    return RemoteReconcileReport(
        scope=RemoteReconcileScope(job_id="job-secret"),
        local_object_count=len(findings),
        remote_object_count=0,
        findings=tuple(findings),
    )


def _finding(
    status: str,
    remote_object_id: str,
    *,
    object_type: str = "archive",
    remote_size: int | None = None,
    remote_md5: str = "",
    remote_fs_id: int | None = None,
) -> RemoteReconcileFinding:
    return RemoteReconcileFinding(
        status=status,
        object_type=object_type,
        remote_path=REMOTE_PATH,
        suggestion="test",
        job_id="job-secret",
        archive_id="archive-secret",
        archive_sha256="a" * 64,
        local_remote_object_id=remote_object_id,
        local_size=10,
        remote_size=remote_size,
        local_md5="b" * 32,
        remote_md5=remote_md5,
        local_fs_id=123,
        remote_fs_id=remote_fs_id,
    )


def _insert_remote_object(store: SQLiteClientStore) -> None:
    with store.transaction() as conn:
        payload = build_version_fields(
            entity_payload={
                "remote_object_id": "remote-archive",
                "entity_id": "remote_object_archive",
                "object_type": "archive",
                "job_id": "job-secret",
                "device_id": "device-secret",
                "archive_id": "archive-secret",
                "archive_sha256": "a" * 64,
                "remote_path": REMOTE_PATH,
                "size_bytes": 10,
                "md5": "b" * 32,
                "sha256": "a" * 64,
                "fs_id": 123,
                "status": "remote_created",
                "created_at": NOW,
            },
            updated_by_device_id="device-secret",
            now=NOW,
            revision_id="rev-1",
        )
        store.put_remote_object(conn, payload)
