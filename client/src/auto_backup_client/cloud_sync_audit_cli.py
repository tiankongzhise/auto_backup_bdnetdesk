from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from auto_backup_client.baidu.cloud_api import BaiduCloudClient, CloudAPIError
from auto_backup_client.baidu.models import EntitySummary, SyncRevisionEvent, SyncRevisionResult
from auto_backup_client.device_credentials import DeviceCredentialStoreError, resolve_or_register_device_credentials
from auto_backup_client.sqlite_store import build_version_fields, new_id, sync_payload, utc_now_iso


AUDIT_ENTITY_TYPE = "release_sync_audits"
AUDIT_PURPOSE = "p3_14_cloud_sync_truth_probe"


@dataclass(frozen=True)
class CloudSyncAuditResult:
    credential_source: str
    entity_id: str
    event_id: str
    revision_id: str
    canonical_record_sha256: str
    first_status: str
    duplicate_status: str
    summary_matched: bool
    duplicate_verified: bool

    @property
    def truthful(self) -> bool:
        return self.first_status == "synced" and self.summary_matched and self.duplicate_verified


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="真实云服务 Cloud Sync 同步真实性审计探针。")
    parser.add_argument("--base-url", default=os.environ.get("CLOUD_API_BASE_URL", "https://backup.baichengedu.com"))
    parser.add_argument(
        "--device-token-env",
        default="CLOUD_API_DEVICE_TOKEN",
        help="读取 Device Token 的环境变量名；未设置时复用本机 DPAPI 凭据。",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--probe-label", default="", help="可选审计标签；只进入无敏感云端 payload，不在输出中回显。")

    args = parser.parse_args(argv)
    try:
        result = run_cloud_sync_audit(args)
    except (CloudAPIError, DeviceCredentialStoreError, ValueError, RuntimeError, OSError, sqlite3.Error, httpx.HTTPError) as exc:
        _print(f"操作失败: {_safe_error_summary(exc)}")
        return 1

    _print(f"Device Token 来源: {result.credential_source}")
    _print(f"probe_entity_id_sha256: {_sha256_text(result.entity_id)}")
    _print(f"probe_event_id_sha256: {_sha256_text(result.event_id)}")
    _print(f"probe_revision_id: {result.revision_id}")
    _print(f"probe_record_sha256: {result.canonical_record_sha256}")
    _print(f"first_sync_status: {result.first_status}")
    _print(f"summary_matched: {str(result.summary_matched).lower()}")
    _print(f"duplicate_sync_status: {result.duplicate_status}")
    _print(f"duplicate_verified: {str(result.duplicate_verified).lower()}")
    _print(f"cloud_sync_truthful: {str(result.truthful).lower()}")
    return 0 if result.truthful else 1


def run_cloud_sync_audit(args: argparse.Namespace) -> CloudSyncAuditResult:
    if args.timeout <= 0:
        raise ValueError("timeout must be greater than zero")

    credentials, source = _resolve_credentials(args)
    device_id = _require_device_id(credentials)
    now = utc_now_iso()
    event = _build_probe_event(device_id=device_id, now=now, probe_label=args.probe_label)

    with httpx.Client(timeout=args.timeout) as http_client:
        _check_readyz(args.base_url, http_client)
        cloud = BaiduCloudClient(args.base_url, credentials.device_token, http_client=http_client)
        first = _single_result(cloud.sync_revisions([event]))
        summary = cloud.get_entity_summary(event.entity_id)
        duplicate = _single_result(cloud.sync_revisions([event]))
        duplicate_summary = cloud.get_entity_summary(event.entity_id)

    summary_matched = _summary_matches_event(summary, event) and _summary_matches_event(duplicate_summary, event)
    duplicate_verified = duplicate.status == "duplicate"
    return CloudSyncAuditResult(
        credential_source=source,
        entity_id=event.entity_id,
        event_id=event.event_id,
        revision_id=event.revision_id,
        canonical_record_sha256=event.canonical_record_sha256,
        first_status=first.status,
        duplicate_status=duplicate.status,
        summary_matched=summary_matched,
        duplicate_verified=duplicate_verified,
    )


def _build_probe_event(*, device_id: str, now: str, probe_label: str) -> SyncRevisionEvent:
    audit_id = new_id("syncaudit")
    entity_id = f"{AUDIT_ENTITY_TYPE}_{audit_id}"
    entity_payload = {
        "audit_id": audit_id,
        "entity_id": entity_id,
        "purpose": AUDIT_PURPOSE,
        "probe_schema_version": 1,
        "created_at": now,
        "probe_label_sha256": _sha256_text(probe_label.strip()) if probe_label.strip() else "",
    }
    payload = build_version_fields(entity_payload=entity_payload, updated_by_device_id=device_id, now=now)
    return SyncRevisionEvent(
        event_id=new_id("evt"),
        entity_type=AUDIT_ENTITY_TYPE,
        entity_id=entity_id,
        revision_id=str(payload["revision_id"]),
        schema_version=int(payload["schema_version"]),
        data_version=int(payload["data_version"]),
        operation="upsert",
        canonical_record_sha256=str(payload["canonical_record_sha256"]),
        payload=sync_payload(payload),
        updated_at=str(payload["updated_at"]),
    )


def _check_readyz(base_url: str, http_client: httpx.Client) -> None:
    response = http_client.get(base_url.rstrip("/") + "/v1/readyz")
    if response.status_code != 200:
        raise CloudAPIError(response.status_code, "not_ready", "cloud readyz did not return 200")
    try:
        data = response.json()
    except ValueError:
        raise CloudAPIError(response.status_code, "invalid_response", "readyz response must be JSON") from None
    if not isinstance(data, Mapping) or str(data.get("status", "")) != "ready":
        raise CloudAPIError(response.status_code, "not_ready", "cloud readyz is not ready")


def _single_result(results: Sequence[SyncRevisionResult]) -> SyncRevisionResult:
    if len(results) != 1:
        raise CloudAPIError(200, "invalid_response", "sync result count must be 1")
    return results[0]


def _summary_matches_event(summary: EntitySummary, event: SyncRevisionEvent) -> bool:
    if (
        summary.revision_id == event.revision_id
        and summary.data_version == event.data_version
        and summary.canonical_record_sha256 == event.canonical_record_sha256
    ):
        return True
    return any(
        revision.revision_id == event.revision_id
        and revision.data_version == event.data_version
        and revision.canonical_record_sha256 == event.canonical_record_sha256
        for revision in summary.recent_revisions
    )


def _resolve_credentials(args: argparse.Namespace):
    token = os.environ.get(args.device_token_env, "").strip()
    return resolve_or_register_device_credentials(cloud_api_base_url=args.base_url, provided_device_token=token)


def _require_device_id(credentials: object) -> str:
    device_id = str(getattr(credentials, "device_id", "")).strip()
    if not device_id:
        raise ValueError("device_id is required")
    return device_id


def _safe_error_summary(exc: Exception) -> str:
    if isinstance(exc, CloudAPIError):
        return f"cloud_api_error status={exc.status_code} code={exc.error_code or 'unknown'}"
    if isinstance(exc, DeviceCredentialStoreError):
        return "device_credential_store_error"
    if isinstance(exc, ValueError):
        allowed = {"device_id is required", "timeout must be greater than zero"}
        message = str(exc)
        return message if message in allowed else "invalid_argument"
    if isinstance(exc, sqlite3.Error):
        return "sqlite_error"
    if isinstance(exc, OSError):
        return "filesystem_error"
    if isinstance(exc, httpx.HTTPError):
        return "http_error"
    return type(exc).__name__


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _print(message: str) -> None:
    print(message, flush=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
