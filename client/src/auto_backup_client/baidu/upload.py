from __future__ import annotations

import hashlib
import io
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from typing import Any, Mapping

import httpx

from auto_backup_client import local_fs


DEFAULT_BACKUP_ROOT_DIR = "/apps/auto_backup_bdnetdesk/backups"
DEFAULT_PAN_API_BASE_URL = "https://pan.baidu.com"
DEFAULT_LOCATE_UPLOAD_BASE_URL = "https://d.pcs.baidu.com"
DEFAULT_UPLOAD_APP_ID = "250528"
MIN_PART_SIZE = 4 * 1024 * 1024
DEFAULT_PART_SIZE = MIN_PART_SIZE
SLICE_MD5_SIZE = 256 * 1024
USER_AGENT = "pan.baidu.com"


class BaiduNetdiskError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 0, error_code: str = "", response_data: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.response_data = dict(response_data or {})


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    sleep_seconds: float = 0.5
    status_codes: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class BaiduQuota:
    total: int
    used: int
    free: int = 0
    expire: bool = False

    @property
    def available(self) -> int:
        return max(0, self.total - self.used)

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "BaiduQuota":
        return cls(
            total=int(data.get("total", 0)),
            used=int(data.get("used", 0)),
            free=int(data.get("free", 0)),
            expire=bool(data.get("expire", False)),
        )


@dataclass(frozen=True)
class BaiduUserInfo:
    request_id: str
    error_code: int
    data: dict[str, Any]

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "BaiduUserInfo":
        payload = data.get("data")
        return cls(
            request_id=str(data.get("request_id", "")),
            error_code=int(data.get("error_code", 0)),
            data=dict(payload) if isinstance(payload, Mapping) else {},
        )


@dataclass(frozen=True)
class FileBlockPart:
    partseq: int
    offset: int
    size: int
    md5: str


@dataclass(frozen=True)
class FileBlockPlan:
    file_path: Path
    size: int
    part_size: int
    content_md5: str
    slice_md5: str
    block_md5s: tuple[str, ...]
    parts: tuple[FileBlockPart, ...]

    def part_by_seq(self, partseq: int) -> FileBlockPart:
        for part in self.parts:
            if part.partseq == partseq:
                return part
        raise BaiduNetdiskError(f"unknown upload partseq: {partseq}", error_code="invalid_partseq")


@dataclass(frozen=True)
class PrecreateResult:
    path: str
    uploadid: str
    return_type: int
    block_list: tuple[int, ...]

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "PrecreateResult":
        raw_block_list = data.get("block_list", [])
        if not isinstance(raw_block_list, list):
            raise BaiduNetdiskError("baidu precreate block_list must be a list", error_code="invalid_response")
        return cls(
            path=str(data.get("path", "")),
            uploadid=str(data.get("uploadid", "")),
            return_type=int(data.get("return_type", 0)),
            block_list=tuple(int(item) for item in raw_block_list),
        )

    def partseqs_to_upload(self, total_parts: int) -> tuple[int, ...]:
        del total_parts
        return tuple(sorted(self.block_list))


@dataclass(frozen=True)
class LocateUploadResult:
    upload_server: str
    servers: tuple[str, ...]


@dataclass(frozen=True)
class UploadPartResult:
    partseq: int
    md5: str


@dataclass(frozen=True)
class CreateFileResult:
    fs_id: int
    path: str
    md5: str
    server_filename: str
    category: int = 0

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "CreateFileResult":
        return cls(
            fs_id=int(data.get("fs_id", 0)),
            path=str(data.get("path", "")),
            md5=str(data.get("md5", "")),
            server_filename=str(data.get("server_filename", "")),
            category=int(data.get("category", 0)),
        )


@dataclass(frozen=True)
class CompleteUploadResult:
    remote_path: str
    plan: FileBlockPlan
    precreate: PrecreateResult
    locate: LocateUploadResult
    uploaded_parts: tuple[UploadPartResult, ...]
    created: CreateFileResult


@dataclass(frozen=True)
class FileManagerResult:
    errno: int
    info: tuple[dict[str, Any], ...]
    request_id: str = ""
    taskid: int = 0

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "FileManagerResult":
        raw_info = data.get("info", [])
        info = tuple(dict(item) for item in raw_info) if isinstance(raw_info, list) else tuple()
        return cls(
            errno=int(data.get("errno", 0)),
            info=info,
            request_id=str(data.get("request_id", "")),
            taskid=int(data.get("taskid", 0)),
        )


@dataclass(frozen=True)
class BaiduFileItem:
    fs_id: int
    path: str
    server_filename: str
    isdir: bool
    size: int
    md5: str = ""
    category: int = 0
    server_ctime: int = 0
    server_mtime: int = 0
    local_ctime: int = 0
    local_mtime: int = 0
    dir_empty: int | None = None

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "BaiduFileItem":
        return cls(
            fs_id=int(data.get("fs_id", 0) or 0),
            path=str(data.get("path", "")),
            server_filename=str(data.get("server_filename", "")),
            isdir=int(data.get("isdir", 0) or 0) == 1,
            size=int(data.get("size", 0) or 0),
            md5=str(data.get("md5", "")),
            category=int(data.get("category", 0) or 0),
            server_ctime=int(data.get("server_ctime", 0) or 0),
            server_mtime=int(data.get("server_mtime", 0) or 0),
            local_ctime=int(data.get("local_ctime", 0) or 0),
            local_mtime=int(data.get("local_mtime", 0) or 0),
            dir_empty=int(data["dir_empty"]) if data.get("dir_empty") is not None else None,
        )


@dataclass(frozen=True)
class BaiduFileListResult:
    errno: int
    items: tuple[BaiduFileItem, ...]
    request_id: str = ""
    has_more: bool = False
    cursor: int = 0

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "BaiduFileListResult":
        raw_items = data.get("list", [])
        items = tuple(BaiduFileItem.from_json(item) for item in raw_items) if isinstance(raw_items, list) else tuple()
        return cls(
            errno=int(data.get("errno", 0) or 0),
            items=items,
            request_id=str(data.get("request_id", "")),
            has_more=bool(int(data.get("has_more", 0) or 0)),
            cursor=int(data.get("cursor", 0) or 0),
        )


@dataclass(frozen=True)
class BaiduFileMeta:
    fs_id: int
    path: str
    filename: str
    isdir: bool
    size: int
    md5: str = ""
    dlink: str = ""

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "BaiduFileMeta":
        return cls(
            fs_id=int(data.get("fs_id", 0) or 0),
            path=str(data.get("path", "")),
            filename=str(data.get("filename") or data.get("server_filename") or ""),
            isdir=int(data.get("isdir", 0) or 0) == 1,
            size=int(data.get("size", 0) or 0),
            md5=str(data.get("md5", "")),
            dlink=str(data.get("dlink", "")),
        )


@dataclass(frozen=True)
class BaiduFileMetasResult:
    items: tuple[BaiduFileMeta, ...]
    request_id: str = ""

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "BaiduFileMetasResult":
        raw_items = data.get("list", [])
        items = tuple(BaiduFileMeta.from_json(item) for item in raw_items) if isinstance(raw_items, list) else tuple()
        return cls(items=items, request_id=str(data.get("request_id", "")))


class BaiduNetdiskClient:
    def __init__(
        self,
        access_token: str,
        *,
        http_client: httpx.Client | None = None,
        timeout: float = 60.0,
        pan_api_base_url: str = DEFAULT_PAN_API_BASE_URL,
        locate_upload_base_url: str = DEFAULT_LOCATE_UPLOAD_BASE_URL,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        token = access_token.strip()
        if not token:
            raise ValueError("baidu access token is required")
        self._access_token = token
        self._client = http_client or httpx.Client(timeout=timeout)
        self._owns_client = http_client is None
        self._pan_api_base_url = pan_api_base_url.rstrip("/")
        self._locate_upload_base_url = locate_upload_base_url.rstrip("/")
        self._retry_policy = retry_policy or RetryPolicy()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "BaiduNetdiskClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def get_quota(self, *, checkfree: bool = True, checkexpire: bool = True) -> BaiduQuota:
        data = self._request_json(
            "GET",
            f"{self._pan_api_base_url}/api/quota",
            params={
                "access_token": self._access_token,
                "checkfree": int(checkfree),
                "checkexpire": int(checkexpire),
            },
        )
        _ensure_baidu_success(data, error_field="errno")
        return BaiduQuota.from_json(data)

    def get_user_info(self, *, device_id: str) -> BaiduUserInfo:
        cleaned_device_id = device_id.strip()
        if not cleaned_device_id:
            raise ValueError("device_id is required")
        data = self._request_json(
            "GET",
            f"{self._pan_api_base_url}/rest/2.0/xpan/nas",
            params={
                "method": "iotqueryuinfo",
                "access_token": self._access_token,
                "device_id": cleaned_device_id,
            },
        )
        _ensure_baidu_success(data, error_field="error_code", message_field="error_msg")
        return BaiduUserInfo.from_json(data)

    def precreate(
        self,
        *,
        remote_path: str,
        size: int,
        block_md5s: tuple[str, ...] | list[str],
        content_md5: str = "",
        slice_md5: str = "",
        uploadid: str = "",
        rtype: int = 0,
        local_ctime: int | None = None,
        local_mtime: int | None = None,
    ) -> PrecreateResult:
        cleaned_path = normalize_baidu_path(remote_path, require_backup_root=False)
        payload: dict[str, Any] = {
            "path": cleaned_path,
            "size": str(_validate_non_negative_int(size, "size")),
            "isdir": "0",
            "autoinit": "1",
            "rtype": str(rtype),
            "block_list": _json_md5_list(block_md5s),
        }
        if content_md5:
            payload["content-md5"] = _validate_md5(content_md5, "content_md5")
        if slice_md5:
            payload["slice-md5"] = _validate_md5(slice_md5, "slice_md5")
        if uploadid:
            payload["uploadid"] = uploadid
        if local_ctime is not None:
            payload["local_ctime"] = str(_validate_non_negative_int(local_ctime, "local_ctime"))
        if local_mtime is not None:
            payload["local_mtime"] = str(_validate_non_negative_int(local_mtime, "local_mtime"))

        data = self._request_json(
            "POST",
            f"{self._pan_api_base_url}/rest/2.0/xpan/file",
            params={"method": "precreate", "access_token": self._access_token},
            data=payload,
        )
        _ensure_baidu_success(data, error_field="errno")
        result = PrecreateResult.from_json(data)
        if not result.uploadid:
            raise BaiduNetdiskError("baidu precreate response missed uploadid", error_code="invalid_response")
        return result

    def locate_upload_server(self, *, remote_path: str, uploadid: str) -> LocateUploadResult:
        cleaned_path = normalize_baidu_path(remote_path, require_backup_root=False)
        cleaned_uploadid = uploadid.strip()
        if not cleaned_uploadid:
            raise ValueError("uploadid is required")
        data = self._request_json(
            "GET",
            f"{self._locate_upload_base_url}/rest/2.0/pcs/file",
            params={
                "method": "locateupload",
                "appid": DEFAULT_UPLOAD_APP_ID,
                "access_token": self._access_token,
                "path": cleaned_path,
                "uploadid": cleaned_uploadid,
                "upload_version": "2.0",
            },
        )
        _ensure_baidu_success(data, error_field="error_code", message_field="error_msg")
        servers = _extract_upload_servers(data)
        https_servers = tuple(server for server in servers if server.startswith("https://"))
        if not https_servers:
            raise BaiduNetdiskError("baidu locateupload response missed https upload server", error_code="invalid_response")
        return LocateUploadResult(upload_server=https_servers[0].rstrip("/"), servers=https_servers)

    def upload_part(
        self,
        *,
        upload_server: str,
        remote_path: str,
        uploadid: str,
        plan: FileBlockPlan,
        partseq: int,
    ) -> UploadPartResult:
        part = plan.part_by_seq(partseq)
        chunk = _read_file_range(plan.file_path, part.offset, part.size)
        data = self._request_json(
            "POST",
            upload_server.rstrip("/") + "/rest/2.0/pcs/superfile2",
            params={
                "method": "upload",
                "access_token": self._access_token,
                "type": "tmpfile",
                "path": normalize_baidu_path(remote_path, require_backup_root=False),
                "uploadid": uploadid,
                "partseq": str(partseq),
            },
            files={"file": ("part", io.BytesIO(chunk), "application/octet-stream")},
        )
        _ensure_baidu_success(data, error_field="errno")
        returned_md5 = str(data.get("md5", "")).lower()
        if returned_md5 != part.md5:
            raise BaiduNetdiskError("baidu superfile2 part md5 mismatch", error_code="part_md5_mismatch")
        return UploadPartResult(partseq=partseq, md5=returned_md5)

    def create_file(
        self,
        *,
        remote_path: str,
        size: int,
        block_md5s: tuple[str, ...] | list[str],
        uploadid: str,
        rtype: int = 0,
        local_ctime: int | None = None,
        local_mtime: int | None = None,
    ) -> CreateFileResult:
        payload: dict[str, Any] = {
            "path": normalize_baidu_path(remote_path, require_backup_root=False),
            "size": str(_validate_non_negative_int(size, "size")),
            "isdir": "0",
            "block_list": _json_md5_list(block_md5s),
            "uploadid": uploadid.strip(),
            "rtype": str(rtype),
        }
        if not payload["uploadid"]:
            raise ValueError("uploadid is required")
        if local_ctime is not None:
            payload["local_ctime"] = str(_validate_non_negative_int(local_ctime, "local_ctime"))
        if local_mtime is not None:
            payload["local_mtime"] = str(_validate_non_negative_int(local_mtime, "local_mtime"))
        data = self._request_json(
            "POST",
            f"{self._pan_api_base_url}/rest/2.0/xpan/file",
            params={"method": "create", "access_token": self._access_token},
            data=payload,
        )
        _ensure_baidu_success(data, error_field="errno")
        return CreateFileResult.from_json(data)

    def delete_files(self, remote_paths: list[str] | tuple[str, ...], *, async_mode: int = 0) -> FileManagerResult:
        cleaned_paths = [normalize_baidu_path(path, require_backup_root=False) for path in remote_paths]
        if not cleaned_paths:
            raise ValueError("remote_paths must not be empty")
        data = self._request_json(
            "POST",
            f"{self._pan_api_base_url}/rest/2.0/xpan/file",
            params={
                "method": "filemanager",
                "access_token": self._access_token,
                "opera": "delete",
            },
            data={
                "async": str(_validate_non_negative_int(async_mode, "async")),
                "filelist": json.dumps(cleaned_paths, ensure_ascii=False, separators=(",", ":")),
            },
        )
        _ensure_baidu_success(data, error_field="errno")
        return FileManagerResult.from_json(data)

    def list_dir(
        self,
        *,
        remote_dir: str,
        start: int = 0,
        limit: int = 1000,
        order: str = "name",
        desc: bool = False,
        web: bool = False,
        folder: bool | None = None,
        showempty: bool | None = None,
    ) -> BaiduFileListResult:
        params: dict[str, Any] = {
            "method": "list",
            "access_token": self._access_token,
            "dir": normalize_baidu_path(remote_dir, require_backup_root=False),
            "start": str(_validate_non_negative_int(start, "start")),
            "limit": str(_validate_positive_int(limit, "limit")),
            "order": _validate_list_order(order),
            "desc": int(desc),
            "web": int(web),
        }
        if folder is not None:
            params["folder"] = int(folder)
        if showempty is not None:
            params["showempty"] = int(showempty)
        data = self._request_json("GET", f"{self._pan_api_base_url}/rest/2.0/xpan/file", params=params)
        _ensure_baidu_success(data, error_field="errno")
        return BaiduFileListResult.from_json(data)

    def list_all(
        self,
        *,
        remote_path: str,
        start: int = 0,
        limit: int = 1000,
        recursion: bool = True,
        web: bool = False,
    ) -> BaiduFileListResult:
        params: dict[str, Any] = {
            "method": "listall",
            "access_token": self._access_token,
            "path": normalize_baidu_path(remote_path, require_backup_root=False),
            "start": str(_validate_non_negative_int(start, "start")),
            "limit": str(_validate_positive_int(limit, "limit")),
            "recursion": int(recursion),
            "web": int(web),
        }
        data = self._request_json("GET", f"{self._pan_api_base_url}/rest/2.0/xpan/multimedia", params=params)
        _ensure_baidu_success(data, error_field="errno")
        return BaiduFileListResult.from_json(data)

    def iter_list_all(self, *, remote_path: str, page_limit: int = 1000, recursion: bool = True) -> tuple[BaiduFileItem, ...]:
        items: list[BaiduFileItem] = []
        start = 0
        while True:
            page = self.list_all(remote_path=remote_path, start=start, limit=page_limit, recursion=recursion)
            items.extend(page.items)
            if not page.has_more:
                break
            start = page.cursor or (start + len(page.items))
            if not page.items:
                break
        return tuple(items)

    def file_metas(self, fs_ids: tuple[int, ...] | list[int], *, dlink: bool = False) -> BaiduFileMetasResult:
        cleaned_ids = tuple(_validate_positive_int(value, "fs_id") for value in fs_ids)
        if not cleaned_ids:
            raise ValueError("fs_ids must not be empty")
        if len(cleaned_ids) > 100:
            raise ValueError("fs_ids may contain at most 100 items")
        data = self._request_json(
            "GET",
            f"{self._pan_api_base_url}/rest/2.0/xpan/multimedia",
            params={
                "method": "filemetas",
                "access_token": self._access_token,
                "fsids": json.dumps(list(cleaned_ids), separators=(",", ":")),
                "dlink": int(dlink),
            },
        )
        _ensure_baidu_success(data, error_field="errno")
        return BaiduFileMetasResult.from_json(data)

    def download_dlink(self, dlink: str, target_path: str | Path, *, chunk_size: int = 1024 * 1024) -> None:
        cleaned_dlink = dlink.strip()
        if not cleaned_dlink:
            raise ValueError("dlink is required")
        if chunk_size < 1:
            raise ValueError("chunk_size must be >= 1")
        target = Path(target_path)
        local_fs.make_dirs(target.parent)
        url = _append_access_token(cleaned_dlink, self._access_token)
        try:
            with self._client.stream("GET", url, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as response:
                if response.status_code >= 400:
                    raise BaiduNetdiskError(
                        "baidu netdisk dlink download returned HTTP error",
                        status_code=response.status_code,
                        error_code="download_http_error",
                    )
                with local_fs.open_file(target, "wb") as handle:
                    for chunk in response.iter_bytes(chunk_size=chunk_size):
                        if chunk:
                            handle.write(chunk)
        except httpx.HTTPError as exc:
            raise BaiduNetdiskError("baidu netdisk dlink download failed", error_code="download_http_request_failed") from exc

    def upload_file_complete(
        self,
        *,
        local_path: str | Path,
        remote_path: str,
        part_size: int = DEFAULT_PART_SIZE,
        rtype: int = 0,
    ) -> CompleteUploadResult:
        plan = compute_file_block_plan(local_path, part_size=part_size)
        timestamp = _file_mtime_seconds(plan.file_path)
        precreate = self.precreate(
            remote_path=remote_path,
            size=plan.size,
            block_md5s=plan.block_md5s,
            content_md5=plan.content_md5,
            slice_md5=plan.slice_md5,
            rtype=rtype,
            local_ctime=timestamp,
            local_mtime=timestamp,
        )
        locate = self.locate_upload_server(remote_path=remote_path, uploadid=precreate.uploadid)
        uploaded: list[UploadPartResult] = []
        for partseq in precreate.partseqs_to_upload(len(plan.parts)):
            uploaded.append(
                self.upload_part(
                    upload_server=locate.upload_server,
                    remote_path=remote_path,
                    uploadid=precreate.uploadid,
                    plan=plan,
                    partseq=partseq,
                )
            )
        created = self.create_file(
            remote_path=remote_path,
            size=plan.size,
            block_md5s=plan.block_md5s,
            uploadid=precreate.uploadid,
            rtype=rtype,
            local_ctime=timestamp,
            local_mtime=timestamp,
        )
        return CompleteUploadResult(
            remote_path=normalize_baidu_path(remote_path, require_backup_root=False),
            plan=plan,
            precreate=precreate,
            locate=locate,
            uploaded_parts=tuple(uploaded),
            created=created,
        )

    def _request_json(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("User-Agent", USER_AGENT)
        attempts = max(1, self._retry_policy.max_attempts)
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                response = self._client.request(method, url, headers=headers, **kwargs)
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < attempts:
                    time.sleep(self._retry_policy.sleep_seconds)
                    continue
                raise BaiduNetdiskError("baidu netdisk request failed", error_code="http_request_failed") from exc
            if response.status_code in self._retry_policy.status_codes and attempt < attempts:
                time.sleep(self._retry_policy.sleep_seconds)
                continue
            if response.status_code >= 400:
                request = response.request
                endpoint = f"{request.method} {request.url.host}{request.url.path}"
                raise BaiduNetdiskError(
                    f"baidu netdisk returned HTTP {response.status_code} for {endpoint}",
                    status_code=response.status_code,
                    error_code="http_error",
                    response_data=_response_json_or_empty(response),
                )
            data = _response_json_or_empty(response)
            if not isinstance(data, dict):
                raise BaiduNetdiskError("baidu netdisk response must be a JSON object", error_code="invalid_response")
            return data
        raise BaiduNetdiskError("baidu netdisk request failed", error_code="http_request_failed") from last_exc


def compute_file_block_plan(local_path: str | Path, *, part_size: int = DEFAULT_PART_SIZE) -> FileBlockPlan:
    actual_path = Path(local_path)
    if not local_fs.is_file(actual_path):
        raise FileNotFoundError(str(actual_path))
    _validate_part_size(part_size)
    content_md5 = hashlib.md5()
    slice_md5 = hashlib.md5()
    slice_remaining = SLICE_MD5_SIZE
    parts: list[FileBlockPart] = []
    size = 0
    with local_fs.open_file(actual_path, "rb") as handle:
        partseq = 0
        while True:
            offset = size
            chunk = handle.read(part_size)
            if not chunk:
                break
            size += len(chunk)
            content_md5.update(chunk)
            if slice_remaining > 0:
                slice_chunk = chunk[:slice_remaining]
                slice_md5.update(slice_chunk)
                slice_remaining -= len(slice_chunk)
            parts.append(
                FileBlockPart(
                    partseq=partseq,
                    offset=offset,
                    size=len(chunk),
                    md5=hashlib.md5(chunk).hexdigest(),
                )
            )
            partseq += 1
    if not parts:
        empty_md5 = hashlib.md5(b"").hexdigest()
        parts.append(FileBlockPart(partseq=0, offset=0, size=0, md5=empty_md5))
    return FileBlockPlan(
        file_path=actual_path,
        size=size,
        part_size=part_size,
        content_md5=content_md5.hexdigest(),
        slice_md5=slice_md5.hexdigest(),
        block_md5s=tuple(part.md5 for part in parts),
        parts=tuple(parts),
    )


def build_archive_remote_path(
    *,
    root_dir: str = DEFAULT_BACKUP_ROOT_DIR,
    job_created_at: datetime,
    device_id: str,
    job_id: str,
    archive_seq: int,
    archive_sha256: str,
    suffix: str = ".7z",
) -> str:
    root = normalize_backup_root_dir(root_dir)
    seq = _validate_archive_seq(archive_seq)
    sha256 = _validate_sha256(archive_sha256)
    safe_device = _safe_path_segment(device_id, "device_id")
    safe_job = _safe_path_segment(job_id, "job_id")
    actual_suffix = suffix if suffix.startswith(".") else f".{suffix}"
    if actual_suffix not in {".7z", ".meta.json"}:
        raise ValueError("archive suffix must be .7z or .meta.json")
    created = job_created_at.astimezone(timezone.utc)
    return (
        f"{root}/{created:%Y}/{created:%m}/{created:%d}/"
        f"{safe_device}/{safe_job}/archives/{seq:06d}-{sha256}{actual_suffix}"
    )


def normalize_backup_root_dir(root_dir: str) -> str:
    cleaned = normalize_baidu_path(root_dir, require_backup_root=False)
    if not cleaned.startswith("/apps/"):
        raise ValueError("baidu backup root must be under /apps/{appname}")
    if cleaned == "/apps" or len(cleaned.split("/")) < 3:
        raise ValueError("baidu backup root must include app name under /apps")
    return cleaned.rstrip("/")


def normalize_baidu_path(path: str, *, require_backup_root: bool = True) -> str:
    cleaned = str(path).strip().replace("\\", "/")
    while "//" in cleaned:
        cleaned = cleaned.replace("//", "/")
    if not cleaned.startswith("/"):
        cleaned = "/" + cleaned
    cleaned = cleaned.rstrip("/") if cleaned != "/" else cleaned
    if any(segment in {"", ".", ".."} for segment in cleaned.split("/")[1:]):
        raise ValueError("baidu path contains invalid empty, dot, or parent segment")
    if require_backup_root:
        normalize_backup_root_dir(cleaned)
    return cleaned


def _extract_upload_servers(data: Mapping[str, Any]) -> tuple[str, ...]:
    result: list[str] = []
    raw_servers = data.get("servers", [])
    if isinstance(raw_servers, list):
        for item in raw_servers:
            if isinstance(item, Mapping):
                server = str(item.get("server", "")).strip().rstrip("/")
                if server:
                    result.append(server)
    return tuple(result)


def _ensure_baidu_success(data: Mapping[str, Any], *, error_field: str, message_field: str = "errmsg") -> None:
    if error_field not in data:
        return
    try:
        code = int(data.get(error_field, 0))
    except (TypeError, ValueError):
        code = -1
    if code != 0:
        message = str(data.get(message_field) or data.get("error_msg") or data.get("errmsg") or "baidu netdisk error")
        raise BaiduNetdiskError(message, error_code=str(code), response_data=data)


def _response_json_or_empty(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise BaiduNetdiskError("baidu netdisk response is not valid JSON", status_code=response.status_code, error_code="invalid_json") from exc
    if not isinstance(data, dict):
        raise BaiduNetdiskError("baidu netdisk response must be a JSON object", status_code=response.status_code, error_code="invalid_response")
    return data


def _json_md5_list(values: tuple[str, ...] | list[str]) -> str:
    cleaned = [_validate_md5(value, "block_md5") for value in values]
    if not cleaned:
        raise ValueError("block md5 list must not be empty")
    return json.dumps(cleaned, separators=(",", ":"))


def _validate_md5(value: str, field: str) -> str:
    cleaned = str(value).strip().lower()
    if not re.fullmatch(r"[0-9a-f]{32}", cleaned):
        raise ValueError(f"{field} must be a 32 character lowercase MD5 hex string")
    return cleaned


def _validate_sha256(value: str) -> str:
    cleaned = str(value).strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", cleaned):
        raise ValueError("archive_sha256 must be a 64 character SHA256 hex string")
    return cleaned


def _validate_archive_seq(value: int) -> int:
    actual = int(value)
    if actual < 1:
        raise ValueError("archive_seq must be >= 1")
    return actual


def _validate_non_negative_int(value: int, field: str) -> int:
    actual = int(value)
    if actual < 0:
        raise ValueError(f"{field} must be >= 0")
    return actual


def _validate_positive_int(value: int, field: str) -> int:
    actual = int(value)
    if actual < 1:
        raise ValueError(f"{field} must be >= 1")
    return actual


def _append_access_token(dlink: str, access_token: str) -> str:
    split = urlsplit(dlink)
    query = [(key, value) for key, value in parse_qsl(split.query, keep_blank_values=True) if key != "access_token"]
    query.append(("access_token", access_token))
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))


def _validate_list_order(value: str) -> str:
    cleaned = str(value).strip().lower()
    if cleaned not in {"name", "time", "size"}:
        raise ValueError("baidu list order must be name, time, or size")
    return cleaned


def _validate_part_size(part_size: int) -> None:
    actual = int(part_size)
    if actual < MIN_PART_SIZE:
        raise ValueError("baidu upload part_size must be at least 4 MiB")


def _safe_path_segment(value: str, field: str) -> str:
    cleaned = str(value).strip()
    if not cleaned:
        raise ValueError(f"{field} is required")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", cleaned):
        raise ValueError(f"{field} may only contain letters, numbers, dot, underscore, or hyphen")
    return cleaned


def _read_file_range(path: Path, offset: int, size: int) -> bytes:
    with local_fs.open_file(path, "rb") as handle:
        handle.seek(offset)
        return handle.read(size)


def _file_mtime_seconds(path: Path) -> int:
    return local_fs.mtime_seconds(path)
