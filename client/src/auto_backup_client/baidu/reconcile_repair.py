from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

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
)
from auto_backup_client.sqlite_store import RevisionRecord, SQLiteClientStore


ACTION_NO_ACTION = "no_action"
ACTION_MARK_REMOTE_MISSING = "mark_remote_missing"
ACTION_ACCEPT_BAIDU_METADATA = "accept_baidu_metadata"
ACTION_MANUAL_REBUILD_FROM_BAIDU = "manual_rebuild_from_baidu"
ACTION_RETRY_ACCESS_CHECK = "retry_access_check"
ACTION_UNSUPPORTED = "unsupported_manual_review"

WRITABLE_ACTIONS = frozenset({ACTION_MARK_REMOTE_MISSING, ACTION_ACCEPT_BAIDU_METADATA})
CLI_REPAIR_ACTIONS = frozenset({"safe-local", ACTION_MARK_REMOTE_MISSING, ACTION_ACCEPT_BAIDU_METADATA})
CONFIRM_REPAIR_TEXT = "APPLY_REMOTE_REPAIR"


@dataclass(frozen=True)
class RemoteRepairCandidate:
    status: str
    action: str
    object_type: str
    remote_path: str
    selected: bool
    will_write: bool
    reason: str
    local_remote_object_id: str = ""
    updates: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RemoteRepairPlan:
    report: RemoteReconcileReport
    candidates: tuple[RemoteRepairCandidate, ...]
    action_filter: tuple[str, ...]

    @property
    def selected_candidates(self) -> tuple[RemoteRepairCandidate, ...]:
        return tuple(candidate for candidate in self.candidates if candidate.selected)

    @property
    def selected_writable_candidates(self) -> tuple[RemoteRepairCandidate, ...]:
        return tuple(candidate for candidate in self.candidates if candidate.selected and candidate.will_write)

    @property
    def writable_count(self) -> int:
        return sum(1 for candidate in self.candidates if candidate.will_write)


@dataclass(frozen=True)
class RemoteRepairAppliedRecord:
    local_remote_object_id: str
    revision_id: str
    data_version: int
    action: str


@dataclass(frozen=True)
class RemoteRepairResult:
    dry_run: bool
    candidate_count: int
    writable_count: int
    selected_count: int
    applied_count: int
    applied_records: tuple[RemoteRepairAppliedRecord, ...] = tuple()


class RemoteObjectRepairer:
    def __init__(self, *, store: SQLiteClientStore, updated_by_device_id: str) -> None:
        self._store = store
        self._updated_by_device_id = updated_by_device_id.strip()
        if not self._updated_by_device_id:
            raise ValueError("updated_by_device_id is required")

    def apply(self, plan: RemoteRepairPlan, *, dry_run: bool, now: str | None = None) -> RemoteRepairResult:
        selected = plan.selected_writable_candidates
        if dry_run or not selected:
            return RemoteRepairResult(
                dry_run=dry_run,
                candidate_count=len(plan.candidates),
                writable_count=plan.writable_count,
                selected_count=len(plan.selected_candidates),
                applied_count=0,
            )

        applied: list[RemoteRepairAppliedRecord] = []
        with self._store.transaction() as conn:
            for candidate in selected:
                record = self._store.repair_remote_object(
                    conn,
                    remote_object_id=candidate.local_remote_object_id,
                    updates=candidate.updates,
                    updated_by_device_id=self._updated_by_device_id,
                    now=now,
                )
                applied.append(_applied_record(candidate, record))
        return RemoteRepairResult(
            dry_run=False,
            candidate_count=len(plan.candidates),
            writable_count=plan.writable_count,
            selected_count=len(plan.selected_candidates),
            applied_count=len(applied),
            applied_records=tuple(applied),
        )


def build_remote_repair_plan(
    report: RemoteReconcileReport,
    *,
    action_filter: Iterable[str] | None = None,
) -> RemoteRepairPlan:
    selected_actions = _selected_actions(action_filter)
    candidates = tuple(_candidate_for_finding(finding, selected_actions=selected_actions) for finding in report.findings)
    return RemoteRepairPlan(report=report, candidates=candidates, action_filter=tuple(sorted(selected_actions)))


def action_filter_from_cli(value: str) -> frozenset[str]:
    cleaned = value.strip() or "safe-local"
    if cleaned not in CLI_REPAIR_ACTIONS:
        raise ValueError("repair action is invalid")
    if cleaned == "safe-local":
        return WRITABLE_ACTIONS
    return frozenset({cleaned})


def _candidate_for_finding(finding: RemoteReconcileFinding, *, selected_actions: frozenset[str]) -> RemoteRepairCandidate:
    action, updates, will_write, reason = _repair_intent_for_finding(finding)
    selected = will_write and action in selected_actions
    return RemoteRepairCandidate(
        status=finding.status,
        action=action,
        object_type=finding.object_type,
        remote_path=finding.remote_path,
        selected=selected,
        will_write=will_write,
        reason=reason,
        local_remote_object_id=finding.local_remote_object_id,
        updates=updates,
    )


def _repair_intent_for_finding(finding: RemoteReconcileFinding) -> tuple[str, dict[str, object], bool, str]:
    if finding.status == STATUS_CONSISTENT:
        return ACTION_NO_ACTION, {}, False, "consistent objects do not need repair"
    if finding.status in {STATUS_DB_EXISTS_REMOTE_MISSING, STATUS_REMOTE_META_MISSING}:
        if not finding.local_remote_object_id:
            return ACTION_UNSUPPORTED, {}, False, "missing local remote_objects row cannot be updated"
        return ACTION_MARK_REMOTE_MISSING, {"status": "remote_missing"}, True, "mark local remote object as missing"
    if finding.status in {STATUS_FS_ID_CHANGED, STATUS_REMOTE_SIZE_MISMATCH, STATUS_REMOTE_META_MISMATCH}:
        if not finding.local_remote_object_id:
            return ACTION_UNSUPPORTED, {}, False, "missing local remote_objects row cannot be updated"
        updates: dict[str, object] = {"status": "remote_created"}
        if finding.remote_size is not None:
            updates["size_bytes"] = finding.remote_size
        if finding.remote_md5:
            updates["md5"] = finding.remote_md5
        if finding.remote_fs_id is not None:
            updates["fs_id"] = finding.remote_fs_id
        if set(updates) == {"status"}:
            return ACTION_UNSUPPORTED, {}, False, "remote metadata was not available"
        return ACTION_ACCEPT_BAIDU_METADATA, updates, True, "accept Baidu list/listall metadata into local record"
    if finding.status == STATUS_BAIDU_ONLY:
        return ACTION_MANUAL_REBUILD_FROM_BAIDU, {}, False, "requires manual import or rebuild from Baidu object"
    if finding.status == STATUS_REMOTE_UNREADABLE:
        return ACTION_RETRY_ACCESS_CHECK, {}, False, "retry after Baidu directory access is available"
    return ACTION_UNSUPPORTED, {}, False, "unsupported reconcile status"


def _selected_actions(action_filter: Iterable[str] | None) -> frozenset[str]:
    if action_filter is None:
        return WRITABLE_ACTIONS
    return frozenset(action_filter)


def _applied_record(candidate: RemoteRepairCandidate, record: RevisionRecord) -> RemoteRepairAppliedRecord:
    return RemoteRepairAppliedRecord(
        local_remote_object_id=candidate.local_remote_object_id,
        revision_id=record.revision_id,
        data_version=record.data_version,
        action=candidate.action,
    )
