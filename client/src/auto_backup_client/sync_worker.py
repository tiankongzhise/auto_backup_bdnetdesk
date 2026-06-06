from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from auto_backup_client.baidu.cloud_api import CloudAPIError
from auto_backup_client.baidu.models import SyncRevisionEvent, SyncRevisionResult
from auto_backup_client.sqlite_store import OutboxEvent, SQLiteClientStore, utc_now_iso


SUCCESS_STATUSES = frozenset({"synced", "duplicate"})
CONFLICT_STATUS = "conflict"
REJECTED_STATUS = "rejected"


class RevisionSyncClient(Protocol):
    def sync_revisions(self, events: list[SyncRevisionEvent] | tuple[SyncRevisionEvent, ...]) -> list[SyncRevisionResult]:
        ...


@dataclass(frozen=True)
class SyncWorkerResult:
    selected: int
    sent: int
    synced: int
    conflicts: int
    rejected: int
    retryable: int


class SyncOutboxWorker:
    def __init__(
        self,
        *,
        store: SQLiteClientStore,
        cloud: RevisionSyncClient,
        batch_size: int = 100,
    ) -> None:
        if batch_size < 1 or batch_size > 100:
            raise ValueError("sync batch_size must be between 1 and 100")
        self._store = store
        self._cloud = cloud
        self._batch_size = batch_size

    def run_once(self, *, now: str | None = None) -> SyncWorkerResult:
        actual_now = now or utc_now_iso()
        outbox_events = self._store.list_outbox_events_for_sync(limit=self._batch_size, now=actual_now)
        if not outbox_events:
            return SyncWorkerResult(selected=0, sent=0, synced=0, conflicts=0, rejected=0, retryable=0)

        event_ids = [event.event_id for event in outbox_events]
        self._store.mark_outbox_syncing(event_ids, now=actual_now)
        revision_events = [_to_sync_revision_event(event) for event in outbox_events]
        try:
            results = self._cloud.sync_revisions(revision_events)
        except CloudAPIError as exc:
            self._store.mark_outbox_retryable(event_ids, reason=_cloud_error_reason(exc), now=actual_now)
            return SyncWorkerResult(selected=len(outbox_events), sent=len(outbox_events), synced=0, conflicts=0, rejected=0, retryable=len(outbox_events))
        except Exception as exc:
            self._store.mark_outbox_retryable(event_ids, reason=f"sync_exception:{type(exc).__name__}", now=actual_now)
            return SyncWorkerResult(selected=len(outbox_events), sent=len(outbox_events), synced=0, conflicts=0, rejected=0, retryable=len(outbox_events))

        event_by_id = {event.event_id: event for event in outbox_events}
        synced = 0
        conflicts = 0
        rejected = 0
        retryable = 0
        seen_event_ids: set[str] = set()
        for result in results:
            event = event_by_id.get(result.event_id)
            if event is None:
                continue
            seen_event_ids.add(result.event_id)
            reason = result.reason or result.status
            if result.status in SUCCESS_STATUSES:
                self._store.mark_outbox_success(
                    result.event_id,
                    entity_type=event.entity_type,
                    entity_id=event.entity_id,
                    revision_id=event.revision_id,
                    now=actual_now,
                )
                synced += 1
            elif result.status == CONFLICT_STATUS:
                self._store.mark_outbox_conflict(
                    result.event_id,
                    entity_type=event.entity_type,
                    entity_id=event.entity_id,
                    revision_id=event.revision_id,
                    reason=reason,
                    now=actual_now,
                )
                conflicts += 1
            elif result.status == REJECTED_STATUS:
                self._store.mark_outbox_failed_terminal(result.event_id, reason=reason, now=actual_now)
                rejected += 1
            else:
                self._store.mark_outbox_retryable((result.event_id,), reason=f"unexpected_status:{result.status}", now=actual_now)
                retryable += 1

        missing_event_ids = tuple(event_id for event_id in event_ids if event_id not in seen_event_ids)
        if missing_event_ids:
            self._store.mark_outbox_retryable(missing_event_ids, reason="missing_sync_result", now=actual_now)
            retryable += len(missing_event_ids)

        return SyncWorkerResult(
            selected=len(outbox_events),
            sent=len(outbox_events),
            synced=synced,
            conflicts=conflicts,
            rejected=rejected,
            retryable=retryable,
        )


def _to_sync_revision_event(event: OutboxEvent) -> SyncRevisionEvent:
    return SyncRevisionEvent(
        event_id=event.event_id,
        entity_type=event.entity_type,
        entity_id=event.entity_id,
        revision_id=event.revision_id,
        schema_version=event.schema_version,
        data_version=event.data_version,
        operation=event.operation,
        canonical_record_sha256=event.canonical_record_sha256,
        payload=event.payload,
        updated_at=event.updated_at,
        deleted_at=event.deleted_at,
    )


def _cloud_error_reason(exc: CloudAPIError) -> str:
    if exc.error_code:
        return f"{exc.status_code}:{exc.error_code}"
    return f"{exc.status_code}:cloud_api_error"
