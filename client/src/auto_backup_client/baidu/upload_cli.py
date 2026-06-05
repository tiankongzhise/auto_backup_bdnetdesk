from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import tempfile
import sys
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from auto_backup_client.baidu.auth_workflow import BaiduAuthWorkflow
from auto_backup_client.baidu.cloud_api import BaiduCloudClient, CloudAPIError
from auto_backup_client.baidu.upload import (
    DEFAULT_BACKUP_ROOT_DIR,
    DEFAULT_PART_SIZE,
    BaiduNetdiskClient,
    BaiduNetdiskError,
    build_archive_remote_path,
    compute_file_block_plan,
)
from auto_backup_client.device_credentials import resolve_or_register_device_credentials


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="使用真实百度网盘 API 验证上传核心链路。")
    parser.add_argument("--base-url", default=os.environ.get("CLOUD_API_BASE_URL", "https://backup.baichengedu.com"))
    parser.add_argument(
        "--device-token-env",
        default="CLOUD_API_DEVICE_TOKEN",
        help="读取 Device Token 的环境变量名；未设置时复用本机 DPAPI 凭据。",
    )
    parser.add_argument("--password-env", default="", help="从指定环境变量读取授权密码。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    quota_parser = subparsers.add_parser("quota", help="解密已选账号 token，并读取百度网盘容量。")
    quota_parser.add_argument("account_id", nargs="?", default="", help="账号 ID；省略时使用当前设备已选择账号。")

    uinfo_parser = subparsers.add_parser("uinfo", help="解密已选账号 token，并读取百度用户信息。")
    uinfo_parser.add_argument("account_id", nargs="?", default="", help="账号 ID；省略时使用当前设备已选择账号。")

    upload_parser = subparsers.add_parser("upload-file", help="上传一个小文件，验证 precreate -> superfile2 -> create。")
    upload_parser.add_argument("local_path", help="要上传的本地文件路径。")
    upload_parser.add_argument("account_id", nargs="?", default="", help="账号 ID；省略时使用当前设备已选择账号。")
    upload_parser.add_argument("--root-dir", default=DEFAULT_BACKUP_ROOT_DIR, help="百度备份根目录，必须位于 /apps/{appname} 下。")
    upload_parser.add_argument("--device-id", default="", help="远端路径中的 device_id；默认使用本机云端 device_id。")
    upload_parser.add_argument("--job-id", default="", help="远端路径中的 job_id；默认生成 upload-cli-*。")
    upload_parser.add_argument("--archive-seq", type=int, default=1)
    upload_parser.add_argument("--part-size-mib", type=int, default=4, choices=(4, 16, 32))
    upload_parser.add_argument("--check-quota", action="store_true", help="上传前读取容量并检查剩余空间。")

    batch_parser = subparsers.add_parser("real-batch", help="生成测试文件并真实上传/冲突/删除清理。")
    batch_parser.add_argument("account_id", nargs="?", default="", help="账号 ID；省略时使用当前设备已选择账号。")
    batch_parser.add_argument("--root-dir", default=DEFAULT_BACKUP_ROOT_DIR, help="百度备份根目录，必须位于 /apps/{appname} 下。")
    batch_parser.add_argument("--device-id", default="", help="远端路径中的 device_id；默认使用本机云端 device_id。")
    batch_parser.add_argument("--job-id", default="", help="远端路径中的 job_id；默认生成 upload-realtest-*。")
    batch_parser.add_argument("--work-dir", default="", help="生成本地测试文件的目录；默认使用系统临时目录。")
    batch_parser.add_argument("--keep-remote", action="store_true", help="保留远端测试文件，默认上传后删除。")

    args = parser.parse_args(argv)
    try:
        if args.command == "quota":
            return _quota(args)
        if args.command == "uinfo":
            return _uinfo(args)
        if args.command == "upload-file":
            return _upload_file(args)
        if args.command == "real-batch":
            return _real_batch(args)
    except (CloudAPIError, BaiduNetdiskError, ValueError, OSError) as exc:
        _print(f"操作失败: {exc}")
        return 1
    return 2


def _quota(args: argparse.Namespace) -> int:
    password = _read_authorization_password(args.password_env)
    credentials, source = _resolve_credentials(args)
    with BaiduCloudClient(args.base_url, credentials.device_token, timeout=30.0) as cloud:
        decrypted = _decrypt_selected_token(cloud, args.account_id, password)
    with BaiduNetdiskClient(decrypted.token.access_token, timeout=30.0) as baidu:
        quota = baidu.get_quota()
    _print(f"Device Token 来源: {source}")
    _print(f"account_id: {decrypted.encrypted.account_id}")
    _print(f"token_version: {decrypted.encrypted.token_version}")
    _print(f"quota_total_bytes: {quota.total}")
    _print(f"quota_used_bytes: {quota.used}")
    _print(f"quota_available_bytes: {quota.available}")
    _print(f"quota_expire_soon: {quota.expire}")
    return 0


def _uinfo(args: argparse.Namespace) -> int:
    password = _read_authorization_password(args.password_env)
    credentials, source = _resolve_credentials(args)
    with BaiduCloudClient(args.base_url, credentials.device_token, timeout=30.0) as cloud:
        decrypted = _decrypt_selected_token(cloud, args.account_id, password)
    with BaiduNetdiskClient(decrypted.token.access_token, timeout=30.0) as baidu:
        info = baidu.get_user_info(device_id=credentials.device_id or "auto_backup_bdnetdesk")
    _print(f"Device Token 来源: {source}")
    _print(f"account_id: {decrypted.encrypted.account_id}")
    _print(f"token_version: {decrypted.encrypted.token_version}")
    _print(f"baidu_error_code: {info.error_code}")
    _print(f"has_privilege: {info.data.get('has_privilege', '')}")
    _print(f"is_svip: {info.data.get('is_svip', '')}")
    _print(f"is_iot_svip: {info.data.get('is_iot_svip', '')}")
    return 0


def _upload_file(args: argparse.Namespace) -> int:
    password = _read_authorization_password(args.password_env)
    credentials, source = _resolve_credentials(args)
    local_path = Path(args.local_path)
    plan = compute_file_block_plan(local_path, part_size=args.part_size_mib * 1024 * 1024)
    archive_sha256 = _file_sha256(local_path)
    remote_path = build_archive_remote_path(
        root_dir=args.root_dir,
        job_created_at=datetime.now(timezone.utc),
        device_id=args.device_id.strip() or credentials.device_id or "unknown-device",
        job_id=args.job_id.strip() or f"upload-cli-{uuid.uuid4().hex[:12]}",
        archive_seq=args.archive_seq,
        archive_sha256=archive_sha256,
        suffix=".7z",
    )
    with BaiduCloudClient(args.base_url, credentials.device_token, timeout=30.0) as cloud:
        decrypted = _decrypt_selected_token(cloud, args.account_id, password)
    with BaiduNetdiskClient(decrypted.token.access_token, timeout=90.0) as baidu:
        if args.check_quota:
            quota = baidu.get_quota()
            if quota.available < plan.size:
                raise BaiduNetdiskError("baidu netdisk available quota is smaller than local file size", error_code="quota_not_enough")
        result = baidu.upload_file_complete(
            local_path=local_path,
            remote_path=remote_path,
            part_size=args.part_size_mib * 1024 * 1024,
            rtype=0,
        )
    _print(f"Device Token 来源: {source}")
    _print(f"account_id: {decrypted.encrypted.account_id}")
    _print(f"token_version: {decrypted.encrypted.token_version}")
    _print(f"local_size_bytes: {plan.size}")
    _print(f"part_count: {len(plan.parts)}")
    _print(f"uploaded_part_count: {len(result.uploaded_parts)}")
    _print(f"remote_path_sha256: {hashlib.sha256(result.remote_path.encode('utf-8')).hexdigest()}")
    _print(f"fs_id: {result.created.fs_id}")
    _print(f"remote_md5: {result.created.md5}")
    _print("上传链路完成: precreate -> locateupload -> superfile2 -> create")
    return 0


def _real_batch(args: argparse.Namespace) -> int:
    password = _read_authorization_password(args.password_env)
    credentials, source = _resolve_credentials(args)
    device_id = args.device_id.strip() or credentials.device_id or "unknown-device"
    job_id = args.job_id.strip() or f"upload-realtest-{uuid.uuid4().hex[:12]}"
    root_dir = args.root_dir
    remote_paths: list[str] = []
    cleanup_errors: list[str] = []

    temp_parent = Path(args.work_dir) if args.work_dir else None
    if temp_parent is not None:
        temp_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="auto-backup-baidu-upload-", dir=str(temp_parent) if temp_parent else None) as temp_dir:
        temp_root = Path(temp_dir)
        small_file = temp_root / "small.7z"
        multipart_file = temp_root / "multipart.7z"
        small_file.write_bytes(b"auto-backup real upload small file\n" + os.urandom(1024))
        multipart_file.write_bytes((b"A" * (4 * 1024 * 1024)) + b"tail-" + os.urandom(1024))

        with BaiduCloudClient(args.base_url, credentials.device_token, timeout=30.0) as cloud:
            decrypted = _decrypt_selected_token(cloud, args.account_id, password)
        with BaiduNetdiskClient(decrypted.token.access_token, timeout=120.0) as baidu:
            quota = baidu.get_quota()
            total_size = small_file.stat().st_size + multipart_file.stat().st_size
            if quota.available < total_size:
                raise BaiduNetdiskError("baidu netdisk available quota is smaller than generated test files", error_code="quota_not_enough")
            try:
                small_remote = _remote_path_for_file(
                    local_path=small_file,
                    root_dir=root_dir,
                    device_id=device_id,
                    job_id=job_id,
                    archive_seq=1,
                )
                multipart_remote = _remote_path_for_file(
                    local_path=multipart_file,
                    root_dir=root_dir,
                    device_id=device_id,
                    job_id=job_id,
                    archive_seq=2,
                )

                small_result = baidu.upload_file_complete(local_path=small_file, remote_path=small_remote, part_size=4 * 1024 * 1024, rtype=0)
                remote_paths.append(small_result.remote_path)
                multipart_result = baidu.upload_file_complete(local_path=multipart_file, remote_path=multipart_remote, part_size=4 * 1024 * 1024, rtype=0)
                remote_paths.append(multipart_result.remote_path)

                conflict_ok = False
                try:
                    baidu.upload_file_complete(local_path=small_file, remote_path=small_remote, part_size=4 * 1024 * 1024, rtype=0)
                except BaiduNetdiskError as exc:
                    conflict_ok = True
                    _print(f"conflict_check_error_code: {exc.error_code}")
                if not conflict_ok:
                    raise BaiduNetdiskError("expected same-path upload conflict did not happen", error_code="conflict_not_detected")

                _print(f"Device Token 来源: {source}")
                _print(f"account_id: {decrypted.encrypted.account_id}")
                _print(f"token_version: {decrypted.encrypted.token_version}")
                _print(f"quota_available_bytes_before: {quota.available}")
                _print(f"small_file_size: {small_result.plan.size}")
                _print(f"small_file_parts: {len(small_result.plan.parts)}")
                _print(f"small_file_uploaded_parts: {len(small_result.uploaded_parts)}")
                _print(f"small_file_fs_id: {small_result.created.fs_id}")
                _print(f"multipart_file_size: {multipart_result.plan.size}")
                _print(f"multipart_file_parts: {len(multipart_result.plan.parts)}")
                _print(f"multipart_file_uploaded_parts: {len(multipart_result.uploaded_parts)}")
                _print(f"multipart_file_fs_id: {multipart_result.created.fs_id}")
                _print(f"remote_batch_prefix_sha256: {hashlib.sha256((root_dir + '/' + device_id + '/' + job_id).encode('utf-8')).hexdigest()}")
                _print("真实上传批测完成: quota -> small upload -> multipart upload -> conflict")
            finally:
                if remote_paths and not args.keep_remote:
                    try:
                        delete_result = baidu.delete_files(tuple(remote_paths), async_mode=0)
                        _print(f"cleanup_delete_errno: {delete_result.errno}")
                        _print(f"cleanup_deleted_count: {len(remote_paths)}")
                    except Exception as exc:
                        cleanup_errors.append(str(exc))
                        _print(f"cleanup_failed: {exc}")
    if cleanup_errors:
        return 1
    return 0


def _resolve_credentials(args: argparse.Namespace) -> tuple[object, str]:
    token = os.environ.get(args.device_token_env, "").strip()
    return resolve_or_register_device_credentials(cloud_api_base_url=args.base_url, provided_device_token=token)


def _decrypt_selected_token(cloud: BaiduCloudClient, account_id: str, password: str):
    workflow = BaiduAuthWorkflow(cloud)
    actual_account_id = account_id.strip() or _selected_account_id(workflow)
    return workflow.decrypt_password_token(actual_account_id, authorization_password=password)


def _selected_account_id(workflow: BaiduAuthWorkflow) -> str:
    selected = [account for account in workflow.load_accounts() if account.selected]
    if not selected:
        raise ValueError("account_id is required because current device has no selected Baidu account")
    return selected[0].account_id


def _read_authorization_password(password_env: str) -> str:
    password = os.environ.get(password_env, "") if password_env else ""
    if not password:
        password = getpass.getpass("授权密码（不回显，不写入文件）: ")
    if not password:
        raise ValueError("authorization password is required")
    return password


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remote_path_for_file(*, local_path: Path, root_dir: str, device_id: str, job_id: str, archive_seq: int) -> str:
    return build_archive_remote_path(
        root_dir=root_dir,
        job_created_at=datetime.now(timezone.utc),
        device_id=device_id,
        job_id=job_id,
        archive_seq=archive_seq,
        archive_sha256=_file_sha256(local_path),
        suffix=".7z",
    )


def _print(message: str) -> None:
    print(message, flush=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
