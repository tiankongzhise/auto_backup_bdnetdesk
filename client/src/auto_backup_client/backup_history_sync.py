from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from auto_backup_client.baidu.models import BackupHistoryEntity
from auto_backup_client.sqlite_store import SQLiteClientStore


class BackupHistoryClient(Protocol):
    def list_backup_history(self, *, limit: int = 5000):
        ...


ENTITY_ORDER = {
    "backup_jobs": 1,
    "backup_sources": 2,
    "file_items": 3,
    "folder_items": 4,
    "content_objects": 5,
    "content_references": 6,
    "archives": 7,
    "archive_members": 8,
    "remote_objects": 9,
}


@dataclass(frozen=True)
class BackupHistorySyncResult:
    imported_count: int
    skipped_count: int


def sync_device_backup_history(
    *,
    store: SQLiteClientStore,
    cloud: BackupHistoryClient,
    limit: int = 5000,
) -> BackupHistorySyncResult:
    history = cloud.list_backup_history(limit=limit)
    imported = 0
    skipped = 0
    entities = sorted(history.entities, key=lambda item: (ENTITY_ORDER.get(item.entity_type, 99), item.entity_id))
    with store.transaction() as conn:
        for entity in entities:
            if _import_entity(store, conn, entity):
                imported += 1
            else:
                skipped += 1
    return BackupHistorySyncResult(imported_count=imported, skipped_count=skipped)


def _import_entity(store: SQLiteClientStore, conn, entity: BackupHistoryEntity) -> bool:
    payload = _payload_for_local_import(entity)
    entity_type = entity.entity_type
    try:
        if entity_type == "backup_jobs":
            payload = _filter_row_to_table(conn, entity_type, payload)
            store.put_backup_job(conn, payload, enqueue=False)
        elif entity_type == "backup_sources":
            payload = _filter_row_to_table(conn, entity_type, payload)
            store.put_backup_source(conn, payload, enqueue=False)
        elif entity_type == "content_objects":
            payload = _filter_row_to_table(conn, entity_type, payload)
            store.put_content_object(conn, payload, enqueue=False)
        elif entity_type == "content_references":
            payload = _filter_row_to_table(conn, entity_type, payload)
            store.put_content_reference(conn, payload, enqueue=False)
        elif entity_type == "archives":
            payload = _filter_row_to_table(conn, entity_type, payload)
            store.put_archive(conn, payload, enqueue=False)
        elif entity_type == "archive_members":
            payload = _filter_row_to_table(conn, entity_type, payload)
            store.put_archive_member(conn, payload, enqueue=False)
        elif entity_type == "remote_objects":
            payload = _filter_row_to_table(conn, entity_type, payload)
            store.put_remote_object(conn, payload, enqueue=False)
        elif entity_type in {"file_items", "folder_items"}:
            table = entity_type
            key = "file_item_id" if entity_type == "file_items" else "folder_item_id"
            payload = _filter_row_to_table(conn, table, payload)
            _upsert_raw(conn, table, payload, key)
        else:
            return False
    except Exception:
        return False
    return True


def _payload_for_local_import(entity: BackupHistoryEntity) -> dict[str, object]:
    payload: dict[str, object] = dict(entity.payload)
    payload.setdefault("entity_id", entity.entity_id)
    payload.setdefault("revision_id", entity.revision_id)
    payload.setdefault("schema_version", 1)
    payload.setdefault("data_version", entity.data_version)
    payload.setdefault("canonical_record_sha256", entity.canonical_record_sha256)
    payload.setdefault("updated_by_device_id", entity.updated_by_device_id)
    payload["sync_status"] = "synced"
    payload["last_synced_revision_id"] = entity.revision_id or payload.get("last_synced_revision_id", "")

    entity_type = entity.entity_type
    if entity_type == "backup_sources":
        payload.setdefault("local_path", _cloud_local_path("backup_source", str(payload.get("backup_source_id") or entity.entity_id)))
    elif entity_type in {"file_items", "folder_items"}:
        key = "file_item_id" if entity_type == "file_items" else "folder_item_id"
        payload.setdefault("local_path", _cloud_local_path(key, str(payload.get(key) or entity.entity_id)))
    elif entity_type == "content_references":
        payload.setdefault(
            "local_path",
            _cloud_local_path("content_reference", str(payload.get("content_reference_id") or entity.entity_id)),
        )
    elif entity_type == "archives":
        payload.setdefault("local_archive_path", "")
    return payload


def _cloud_local_path(kind: str, identifier: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in identifier.strip())
    return f"cloud-history://{kind}/{cleaned or 'unknown'}"


def _filter_row_to_table(conn, table: str, row: dict[str, object]) -> dict[str, object]:
    columns = {str(info[1]) for info in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    return {key: value for key, value in row.items() if key in columns}


def _upsert_raw(conn, table: str, row: dict[str, object], key_field: str) -> None:
    if key_field not in row:
        raise ValueError("history payload is missing primary key")
    columns = tuple(row.keys())
    placeholders = ", ".join("?" for _ in columns)
    assignments = ", ".join(f"{column} = excluded.{column}" for column in columns if column != key_field)
    conn.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT({key_field}) DO UPDATE SET {assignments}",
        tuple(row[column] for column in columns),
    )
