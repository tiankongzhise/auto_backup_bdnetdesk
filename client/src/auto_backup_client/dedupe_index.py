from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from typing import Protocol

from auto_backup_client.baidu.cloud_api import CloudAPIError
from auto_backup_client.baidu.models import ContentObject
from auto_backup_client.scan_fingerprints import file_content_id
from auto_backup_client.sqlite_store import SQLiteClientStore, build_version_fields, canonical_record_sha256, utc_now_iso


CONTENT_CLOUD_CANDIDATE = "cloud_duplicate_candidate"
CONTENT_MISSING = "missing"
CONTENT_NOT_CHECKED = "not_checked"
CONTENT_HASH_MISMATCH = "hash_mismatch"
CONTENT_RETRYABLE_ERROR = "retryable_error"


@dataclass(frozen=True)
class ContentIndexResult:
    backup_job_id: str
    content_object_count: int
    reference_count: int
    payload_source_count: int
    local_duplicate_count: int
    cloud_duplicate_candidate_count: int
    skipped_unstable_count: int


@dataclass(frozen=True)
class CloudCandidateResult:
    backup_job_id: str
    checked_content_count: int
    cloud_duplicate_candidate_count: int
    missing_count: int
    hash_mismatch_count: int
    retryable_error_count: int


class CloudContentClient(Protocol):
    def get_content(self, content_id: str) -> ContentObject:
        ...


class DedupeIndexError(ValueError):
    pass


class ContentDedupeIndexer:
    def __init__(self, store: SQLiteClientStore, *, device_id: str) -> None:
        cleaned_device_id = device_id.strip()
        if not cleaned_device_id:
            raise DedupeIndexError("device_id is required")
        self.store = store
        self.device_id = cleaned_device_id

    def build_job_index(self, backup_job_id: str, *, now: str | None = None) -> ContentIndexResult:
        cleaned_job_id = _clean_job_id(backup_job_id)
        actual_now = now or utc_now_iso()
        all_files = self.store.list_file_items(cleaned_job_id)
        stable_files = [row for row in all_files if str(row["scan_status"]) == "full_hashed"]
        skipped_unstable_count = len(all_files) - len(stable_files)
        groups = _group_validated_files(stable_files)

        payload_source_count = 0
        local_duplicate_count = 0
        cloud_duplicate_candidate_count = 0

        with self.store.transaction() as conn:
            stale_content_ids = self.store.replace_content_references_for_job(conn, backup_job_id=cleaned_job_id)
            touched_content_ids: set[str] = set()
            for content_id in sorted(groups):
                touched_content_ids.add(content_id)
                files = sorted(groups[content_id], key=_file_sort_key)
                existing = self.store.get_content_object_for_update(conn, content_id)
                if existing is not None:
                    _validate_existing_object(existing, files[0])
                cloud_status = _cloud_status(existing)
                existing_payload_count = _existing_payload_reference_count(conn, content_id)
                reference_roles = _planned_reference_roles(files, cloud_status, existing_payload_count=existing_payload_count)

                payload_source_count += sum(1 for role, _status in reference_roles if role == "payload_source")
                local_duplicate_count += sum(1 for role, _status in reference_roles if role == "local_duplicate")
                cloud_duplicate_candidate_count += sum(1 for role, _status in reference_roles if role == CONTENT_CLOUD_CANDIDATE)

                for file_row, (role, dedupe_status) in zip(files, reference_roles):
                    self.store.put_content_reference(
                        conn,
                        _content_reference_payload(
                            file_row,
                            device_id=self.device_id,
                            now=actual_now,
                            reference_role=role,
                            dedupe_status=dedupe_status,
                        ),
                    )

                aggregate = _aggregate_reference_counts(conn, content_id)
                content_payload = _content_object_payload(
                    files,
                    existing=existing,
                    device_id=self.device_id,
                    now=actual_now,
                    cloud_status=cloud_status,
                    cloud_latest_entity_id=str(existing["cloud_latest_entity_id"]) if existing is not None else "",
                    cloud_checked_at=str(existing["cloud_checked_at"]) if existing is not None and existing["cloud_checked_at"] else None,
                    reference_count=aggregate["reference_count"],
                    payload_source_count=aggregate["payload_reference_count"],
                    duplicate_count=aggregate["duplicate_reference_count"],
                    last_seen_at=aggregate["last_seen_at"],
                )
                _put_content_object_if_changed(self.store, conn, content_payload, existing)
            count_mismatch_ids = _content_ids_with_count_mismatch(conn)
            for stale_content_id in sorted((stale_content_ids | count_mismatch_ids) - touched_content_ids):
                existing = self.store.get_content_object_for_update(conn, stale_content_id)
                if existing is None:
                    continue
                aggregate = _aggregate_reference_counts(conn, stale_content_id)
                payload = dict(existing)
                payload["reference_count"] = aggregate["reference_count"]
                payload["payload_reference_count"] = aggregate["payload_reference_count"]
                payload["duplicate_reference_count"] = aggregate["duplicate_reference_count"]
                payload["last_seen_at"] = aggregate["last_seen_at"] or payload["last_seen_at"]
                versioned = build_version_fields(
                    entity_payload=payload,
                    updated_by_device_id=self.device_id,
                    data_version=int(existing["data_version"]) + 1,
                    schema_version=int(existing["schema_version"]),
                    now=actual_now,
                    sync_status="sync_pending",
                    deleted_at=existing.get("deleted_at"),
                    last_synced_revision_id=existing.get("last_synced_revision_id"),
                )
                _put_content_object_if_changed(self.store, conn, versioned, existing)

        return ContentIndexResult(
            backup_job_id=cleaned_job_id,
            content_object_count=len(groups),
            reference_count=len(stable_files),
            payload_source_count=payload_source_count,
            local_duplicate_count=local_duplicate_count,
            cloud_duplicate_candidate_count=cloud_duplicate_candidate_count,
            skipped_unstable_count=skipped_unstable_count,
        )

    def refresh_cloud_candidates(
        self,
        backup_job_id: str,
        *,
        cloud_client: CloudContentClient,
        now: str | None = None,
    ) -> CloudCandidateResult:
        cleaned_job_id = _clean_job_id(backup_job_id)
        actual_now = now or utc_now_iso()
        content_ids = sorted({str(row["content_id"]) for row in self.store.list_content_references(cleaned_job_id)})
        status_counts = defaultdict(int)

        for content_id in content_ids:
            local_object = self._get_content_object(content_id)
            if local_object is None:
                continue
            status, latest_entity_id = _query_cloud_candidate(cloud_client, local_object)
            status_counts[status] += 1
            self._update_cloud_candidate(
                cleaned_job_id,
                content_id,
                status=status,
                latest_entity_id=latest_entity_id,
                now=actual_now,
            )

        return CloudCandidateResult(
            backup_job_id=cleaned_job_id,
            checked_content_count=sum(status_counts.values()),
            cloud_duplicate_candidate_count=status_counts[CONTENT_CLOUD_CANDIDATE],
            missing_count=status_counts[CONTENT_MISSING],
            hash_mismatch_count=status_counts[CONTENT_HASH_MISMATCH],
            retryable_error_count=status_counts[CONTENT_RETRYABLE_ERROR],
        )

    def _get_content_object(self, content_id: str) -> dict[str, object] | None:
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT * FROM content_objects WHERE content_id = ?",
                (content_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    def _update_cloud_candidate(
        self,
        backup_job_id: str,
        content_id: str,
        *,
        status: str,
        latest_entity_id: str,
        now: str,
    ) -> None:
        with self.store.transaction() as conn:
            existing = self.store.get_content_object_for_update(conn, content_id)
            if existing is None:
                return
            payload = dict(existing)
            payload["cloud_candidate_status"] = status
            payload["cloud_latest_entity_id"] = latest_entity_id
            payload["cloud_checked_at"] = now
            _rewrite_reference_roles(conn, backup_job_id, content_id, status=status, now=now, device_id=self.device_id)
            aggregate = _aggregate_reference_counts(conn, content_id)
            payload["reference_count"] = aggregate["reference_count"]
            payload["payload_reference_count"] = aggregate["payload_reference_count"]
            payload["duplicate_reference_count"] = aggregate["duplicate_reference_count"]
            versioned = build_version_fields(
                entity_payload=payload,
                updated_by_device_id=self.device_id,
                data_version=int(existing["data_version"]) + 1,
                schema_version=int(existing["schema_version"]),
                now=now,
                sync_status="sync_pending",
                deleted_at=existing.get("deleted_at"),
                last_synced_revision_id=existing.get("last_synced_revision_id"),
            )
            _put_content_object_if_changed(self.store, conn, versioned, existing)


def _clean_job_id(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise DedupeIndexError("backup_job_id is required")
    return cleaned


def _group_validated_files(files: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    for file_row in files:
        content_id = str(file_row["content_id"])
        sha256 = str(file_row["sha256"])
        size_bytes = int(file_row["size_bytes"])
        expected_content_id = file_content_id(size_bytes, sha256)
        if content_id != expected_content_id:
            raise DedupeIndexError("file item content_id does not match sha256 and size")
        if not _is_lower_hex(sha256, 64):
            raise DedupeIndexError("file item sha256 must be 64 lowercase hex characters")
        if size_bytes < 0:
            raise DedupeIndexError("file item size_bytes must not be negative")
        groups[content_id].append(file_row)

    for content_id, rows in groups.items():
        first_sha256 = str(rows[0]["sha256"])
        first_size = int(rows[0]["size_bytes"])
        if any(str(row["sha256"]) != first_sha256 or int(row["size_bytes"]) != first_size for row in rows):
            raise DedupeIndexError(f"content_id collision detected for {content_id}")
    return dict(groups)


def _validate_existing_object(existing: dict[str, object], file_row: dict[str, object]) -> None:
    if str(existing["file_sha256"]) != str(file_row["sha256"]) or int(existing["size_bytes"]) != int(file_row["size_bytes"]):
        raise DedupeIndexError("existing content object conflicts with file sha256 and size")


def _content_object_payload(
    files: list[dict[str, object]],
    *,
    existing: dict[str, object] | None,
    device_id: str,
    now: str,
    cloud_status: str,
    cloud_latest_entity_id: str,
    cloud_checked_at: str | None,
    reference_count: int,
    payload_source_count: int,
    duplicate_count: int,
    last_seen_at: str,
) -> dict[str, object]:
    first = files[0]
    first_seen_at = str(existing["first_seen_at"]) if existing is not None else min(str(row["created_at"]) for row in files)
    created_at = str(existing["created_at"]) if existing is not None else first_seen_at
    entity_payload = {
        "content_id": str(first["content_id"]),
        "entity_id": _content_object_entity_id(device_id, str(first["content_id"])),
        "file_sha256": str(first["sha256"]),
        "size_bytes": int(first["size_bytes"]),
        "md5": str(first["md5"]),
        "reference_count": reference_count,
        "payload_reference_count": payload_source_count,
        "duplicate_reference_count": duplicate_count,
        "cloud_candidate_status": cloud_status,
        "cloud_latest_entity_id": cloud_latest_entity_id,
        "cloud_checked_at": cloud_checked_at,
        "first_seen_at": first_seen_at,
        "last_seen_at": last_seen_at,
        "created_at": created_at,
    }
    return build_version_fields(
        entity_payload=entity_payload,
        updated_by_device_id=device_id,
        data_version=int(existing["data_version"]) + 1 if existing is not None else 1,
        schema_version=int(existing["schema_version"]) if existing is not None else 1,
        now=now,
        sync_status="sync_pending",
        deleted_at=existing.get("deleted_at") if existing is not None else None,
        last_synced_revision_id=existing.get("last_synced_revision_id") if existing is not None else None,
    )


def _put_content_object_if_changed(
    store: SQLiteClientStore,
    conn,
    payload: dict[str, object],
    existing: dict[str, object] | None,
) -> bool:
    if existing is not None and canonical_record_sha256(payload) == str(existing["canonical_record_sha256"]):
        return False
    store.put_content_object(conn, payload)
    return True


def _content_reference_payload(
    file_row: dict[str, object],
    *,
    device_id: str,
    now: str,
    reference_role: str,
    dedupe_status: str,
) -> dict[str, object]:
    content_reference_id = _content_reference_id(str(file_row["file_item_id"]))
    return {
        "content_reference_id": content_reference_id,
        "entity_id": f"content_reference_{content_reference_id}",
        "content_id": str(file_row["content_id"]),
        "file_item_id": str(file_row["file_item_id"]),
        "backup_job_id": str(file_row["backup_job_id"]),
        "backup_source_id": str(file_row["backup_source_id"]),
        "source_seq": int(file_row["source_seq"]),
        "device_id": device_id,
        "local_path": str(file_row["local_path"]),
        "relative_path": str(file_row["relative_path"]),
        "display_name": str(file_row["display_name"]),
        "path_sha256": str(file_row["path_sha256"]),
        "relative_path_sha256": str(file_row["relative_path_sha256"]),
        "file_sha256": str(file_row["sha256"]),
        "size_bytes": int(file_row["size_bytes"]),
        "md5": str(file_row["md5"]),
        "reference_role": reference_role,
        "dedupe_status": dedupe_status,
        "archive_id": "",
        "archive_sha256": "",
        "archive_member_path": "",
        "cleanup_status": "not_cleaned",
        "restore_status": "not_restored",
        "created_at": str(file_row["created_at"]),
        "updated_at": str(file_row["updated_at"]),
        "updated_by_device_id": device_id,
    }


def _planned_reference_roles(
    files: list[dict[str, object]],
    cloud_status: str,
    *,
    existing_payload_count: int,
) -> list[tuple[str, str]]:
    if cloud_status == CONTENT_CLOUD_CANDIDATE:
        return [(CONTENT_CLOUD_CANDIDATE, CONTENT_CLOUD_CANDIDATE) for _file in files]
    if existing_payload_count > 0:
        return [("local_duplicate", "local_duplicate") for _file in files]
    return [("payload_source", "needs_payload")] + [("local_duplicate", "local_duplicate") for _file in files[1:]]


def _rewrite_reference_roles(conn, backup_job_id: str, content_id: str, *, status: str, now: str, device_id: str) -> None:
    rows = conn.execute(
        """
        SELECT content_reference_id
        FROM content_references
        WHERE backup_job_id = ? AND content_id = ?
        ORDER BY source_seq, relative_path, content_reference_id
        """,
        (backup_job_id, content_id),
    ).fetchall()
    if status == CONTENT_CLOUD_CANDIDATE:
        roles = [(CONTENT_CLOUD_CANDIDATE, CONTENT_CLOUD_CANDIDATE) for _row in rows]
    elif _existing_payload_reference_count(conn, content_id, excluding_backup_job_id=backup_job_id) > 0:
        roles = [("local_duplicate", "local_duplicate") for _row in rows]
    else:
        roles = [("payload_source", "needs_payload")] + [("local_duplicate", "local_duplicate") for _row in rows[1:]]
    for row, (role, dedupe_status) in zip(rows, roles):
        conn.execute(
            """
            UPDATE content_references
            SET reference_role = ?,
                dedupe_status = ?,
                updated_at = ?,
                updated_by_device_id = ?
            WHERE content_reference_id = ?
            """,
            (role, dedupe_status, now, device_id, str(row["content_reference_id"])),
        )


def _existing_payload_reference_count(conn, content_id: str, *, excluding_backup_job_id: str = "") -> int:
    if excluding_backup_job_id:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM content_references
            WHERE content_id = ?
              AND reference_role = 'payload_source'
              AND backup_job_id <> ?
            """,
            (content_id, excluding_backup_job_id),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM content_references
            WHERE content_id = ? AND reference_role = 'payload_source'
            """,
            (content_id,),
        ).fetchone()
    return int(row[0]) if row is not None else 0


def _aggregate_reference_counts(conn, content_id: str) -> dict[str, object]:
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS reference_count,
            SUM(CASE WHEN reference_role = 'payload_source' THEN 1 ELSE 0 END) AS payload_reference_count,
            MAX(updated_at) AS last_seen_at
        FROM content_references
        WHERE content_id = ?
        """,
        (content_id,),
    ).fetchone()
    reference_count = int(row["reference_count"]) if row is not None else 0
    payload_reference_count = int(row["payload_reference_count"] or 0) if row is not None else 0
    return {
        "reference_count": reference_count,
        "payload_reference_count": payload_reference_count,
        "duplicate_reference_count": reference_count - payload_reference_count,
        "last_seen_at": str(row["last_seen_at"] or ""),
    }


def _content_ids_with_count_mismatch(conn) -> set[str]:
    rows = conn.execute(
        """
        SELECT content_id, reference_count, payload_reference_count, duplicate_reference_count
        FROM content_objects
        """
    ).fetchall()
    mismatched: set[str] = set()
    for row in rows:
        aggregate = _aggregate_reference_counts(conn, str(row["content_id"]))
        if (
            int(row["reference_count"]) != int(aggregate["reference_count"])
            or int(row["payload_reference_count"]) != int(aggregate["payload_reference_count"])
            or int(row["duplicate_reference_count"]) != int(aggregate["duplicate_reference_count"])
        ):
            mismatched.add(str(row["content_id"]))
    return mismatched


def _query_cloud_candidate(cloud_client: CloudContentClient, local_object: dict[str, object]) -> tuple[str, str]:
    try:
        remote = cloud_client.get_content(str(local_object["content_id"]))
    except CloudAPIError as exc:
        if exc.status_code == 404:
            return CONTENT_MISSING, ""
        if exc.status_code >= 500:
            return CONTENT_RETRYABLE_ERROR, str(local_object.get("cloud_latest_entity_id") or "")
        raise
    if remote.file_sha256 == str(local_object["file_sha256"]) and remote.size_bytes == int(local_object["size_bytes"]):
        return CONTENT_CLOUD_CANDIDATE, remote.latest_entity_id
    return CONTENT_HASH_MISMATCH, remote.latest_entity_id


def _cloud_status(existing: dict[str, object] | None) -> str:
    status = str(existing["cloud_candidate_status"]) if existing is not None else CONTENT_NOT_CHECKED
    return status if status in {CONTENT_CLOUD_CANDIDATE, CONTENT_MISSING, CONTENT_NOT_CHECKED, CONTENT_HASH_MISMATCH, CONTENT_RETRYABLE_ERROR} else CONTENT_NOT_CHECKED


def _file_sort_key(row: dict[str, object]) -> tuple[int, str, str]:
    return int(row["source_seq"]), str(row["relative_path"]).casefold(), str(row["file_item_id"])


def _content_object_entity_id(device_id: str, content_id: str) -> str:
    digest = hashlib.sha256(f"{device_id}\0{content_id}".encode("utf-8")).hexdigest()
    return f"content_object_{digest}"


def _content_reference_id(file_item_id: str) -> str:
    digest = hashlib.sha256(file_item_id.encode("utf-8")).hexdigest()
    return f"cref_{digest}"


def _is_lower_hex(value: str, length: int) -> bool:
    return len(value) == length and all(char in "0123456789abcdef" for char in value)
