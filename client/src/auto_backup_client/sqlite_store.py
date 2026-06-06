from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence


MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations" / "sqlite"
CLIENT_SCHEMA_VERSION = 1
LOCAL_ONLY_SYNC_FIELDS = frozenset({"local_archive_path", "uploadid", "error_message"})
CANONICAL_CONTROL_FIELDS = frozenset(
    {
        "canonical_record_sha256",
        "last_synced_revision_id",
        "revision_id",
        "schema_version",
        "data_version",
        "updated_at",
        "updated_by_device_id",
        "sync_status",
    }
)


@dataclass(frozen=True)
class RevisionRecord:
    event_id: str
    entity_type: str
    entity_id: str
    revision_id: str
    schema_version: int
    data_version: int
    operation: str
    canonical_record_sha256: str
    payload: dict[str, Any]
    updated_at: str
    deleted_at: str | None = None


class SQLiteClientStore:
    def __init__(self, db_path: str | Path, *, migrations_dir: str | Path | None = None) -> None:
        self.db_path = Path(db_path)
        self.migrations_dir = Path(migrations_dir) if migrations_dir is not None else MIGRATIONS_DIR

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def migrate(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    migration_name TEXT PRIMARY KEY,
                    migration_sha256 TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
            for migration in sorted(self.migrations_dir.glob("*.sql")):
                sql = migration.read_text(encoding="utf-8")
                digest = hashlib.sha256(sql.encode("utf-8")).hexdigest()
                row = conn.execute(
                    "SELECT migration_sha256 FROM schema_migrations WHERE migration_name = ?",
                    (migration.name,),
                ).fetchone()
                if row is not None:
                    if row["migration_sha256"] != digest:
                        raise RuntimeError(f"sqlite migration changed after apply: {migration.name}")
                    continue
                conn.executescript(sql)
                conn.execute(
                    """
                    INSERT INTO schema_migrations (migration_name, migration_sha256, applied_at)
                    VALUES (?, ?, ?)
                    """,
                    (migration.name, digest, utc_now_iso()),
                )
            conn.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()
        finally:
            conn.close()

    def enqueue_revision(self, conn: sqlite3.Connection, record: RevisionRecord) -> None:
        payload_json = stable_json_dumps(sync_payload(record.payload))
        conn.execute(
            """
            INSERT INTO sync_outbox (
                event_id,
                entity_type,
                entity_id,
                revision_id,
                operation,
                payload_json,
                status,
                retry_count,
                next_retry_at,
                last_error,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, NULL, NULL, ?, ?)
            """,
            (
                record.event_id,
                record.entity_type,
                record.entity_id,
                record.revision_id,
                record.operation,
                payload_json,
                record.updated_at,
                record.updated_at,
            ),
        )

    def next_data_version(self, conn: sqlite3.Connection, table: str, key_field: str, key_value: str) -> int:
        row = conn.execute(
            f"SELECT data_version FROM {table} WHERE {key_field} = ?",
            (key_value,),
        ).fetchone()
        if row is None:
            return 1
        return int(row["data_version"]) + 1

    def put_upload_session(self, conn: sqlite3.Connection, payload: Mapping[str, Any]) -> RevisionRecord:
        row = dict(payload)
        _put_row(conn, "upload_sessions", row, key_field="upload_session_id")
        record = revision_from_payload(
            entity_type="upload_sessions",
            entity_id=str(row["entity_id"]),
            revision_id=str(row["revision_id"]),
            schema_version=int(row["schema_version"]),
            data_version=int(row["data_version"]),
            canonical_record_sha256=str(row["canonical_record_sha256"]),
            payload=row,
            updated_at=str(row["updated_at"]),
            deleted_at=row.get("deleted_at"),
        )
        self.enqueue_revision(conn, record)
        return record

    def put_upload_part(self, conn: sqlite3.Connection, payload: Mapping[str, Any]) -> RevisionRecord:
        row = dict(payload)
        _put_row(conn, "upload_parts", row, key_field="upload_part_id")
        record = revision_from_payload(
            entity_type="upload_parts",
            entity_id=str(row["entity_id"]),
            revision_id=str(row["revision_id"]),
            schema_version=int(row["schema_version"]),
            data_version=int(row["data_version"]),
            canonical_record_sha256=str(row["canonical_record_sha256"]),
            payload=row,
            updated_at=str(row["updated_at"]),
            deleted_at=row.get("deleted_at"),
        )
        self.enqueue_revision(conn, record)
        return record

    def put_remote_object(self, conn: sqlite3.Connection, payload: Mapping[str, Any]) -> RevisionRecord:
        row = dict(payload)
        _put_row(conn, "remote_objects", row, key_field="remote_object_id")
        record = revision_from_payload(
            entity_type="remote_objects",
            entity_id=str(row["entity_id"]),
            revision_id=str(row["revision_id"]),
            schema_version=int(row["schema_version"]),
            data_version=int(row["data_version"]),
            canonical_record_sha256=str(row["canonical_record_sha256"]),
            payload=row,
            updated_at=str(row["updated_at"]),
            deleted_at=row.get("deleted_at"),
        )
        self.enqueue_revision(conn, record)
        return record

    def get_upload_session_by_remote_path(self, remote_path: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM upload_sessions WHERE remote_archive_path = ?",
                (remote_path,),
            ).fetchone()
            return dict(row) if row is not None else None

    def get_upload_session(self, upload_session_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM upload_sessions WHERE upload_session_id = ?",
                (upload_session_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    def list_upload_parts(self, upload_session_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM upload_parts WHERE upload_session_id = ? ORDER BY partseq",
                (upload_session_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_remote_object_by_path(self, remote_path: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM remote_objects WHERE remote_path = ?",
                (remote_path,),
            ).fetchone()
            return dict(row) if row is not None else None


def revision_from_payload(
    *,
    entity_type: str,
    entity_id: str,
    revision_id: str,
    schema_version: int,
    data_version: int,
    canonical_record_sha256: str,
    payload: Mapping[str, Any],
    updated_at: str,
    deleted_at: Any = None,
    operation: str = "upsert",
) -> RevisionRecord:
    return RevisionRecord(
        event_id=new_id("evt"),
        entity_type=entity_type,
        entity_id=entity_id,
        revision_id=revision_id,
        schema_version=schema_version,
        data_version=data_version,
        operation=operation,
        canonical_record_sha256=canonical_record_sha256,
        payload=dict(payload),
        updated_at=updated_at,
        deleted_at=str(deleted_at) if deleted_at else None,
    )


def build_version_fields(
    *,
    entity_payload: Mapping[str, Any],
    updated_by_device_id: str,
    data_version: int = 1,
    schema_version: int = CLIENT_SCHEMA_VERSION,
    now: str | None = None,
    revision_id: str | None = None,
    sync_status: str = "sync_pending",
    deleted_at: str | None = None,
    last_synced_revision_id: str | None = None,
) -> dict[str, Any]:
    actual_now = now or utc_now_iso()
    actual_revision_id = revision_id or new_revision_id()
    base = {
        **dict(entity_payload),
        "schema_version": schema_version,
        "data_version": data_version,
        "revision_id": actual_revision_id,
        "updated_at": actual_now,
        "updated_by_device_id": updated_by_device_id,
        "sync_status": sync_status,
        "deleted_at": deleted_at,
        "last_synced_revision_id": last_synced_revision_id,
    }
    base["canonical_record_sha256"] = canonical_record_sha256(base)
    return base


def canonical_record_sha256(payload: Mapping[str, Any]) -> str:
    cleaned = {
        key: value
        for key, value in payload.items()
        if key not in CANONICAL_CONTROL_FIELDS and key not in LOCAL_ONLY_SYNC_FIELDS
    }
    return hashlib.sha256(stable_json_dumps(cleaned).encode("utf-8")).hexdigest()


def stable_json_dumps(value: Mapping[str, Any] | Sequence[Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sync_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in LOCAL_ONLY_SYNC_FIELDS}


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def new_revision_id() -> str:
    timestamp_ms = int(time.time() * 1000)
    if timestamp_ms < 0 or timestamp_ms >= 1 << 48:
        raise RuntimeError("current time is outside UUIDv7 timestamp range")
    random_value = int.from_bytes(uuid.uuid4().bytes, "big") & ((1 << 74) - 1)
    uuid_int = (
        (timestamp_ms << 80)
        | (0x7 << 76)
        | (((random_value >> 62) & 0xFFF) << 64)
        | (0b10 << 62)
        | (random_value & ((1 << 62) - 1))
    )
    return str(uuid.UUID(int=uuid_int))


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _put_row(conn: sqlite3.Connection, table: str, row: Mapping[str, Any], *, key_field: str) -> None:
    columns = tuple(row.keys())
    placeholders = ", ".join("?" for _ in columns)
    assignments = ", ".join(f"{column} = excluded.{column}" for column in columns if column != key_field)
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT({key_field}) DO UPDATE SET {assignments}"
    )
    conn.execute(sql, tuple(row[column] for column in columns))
