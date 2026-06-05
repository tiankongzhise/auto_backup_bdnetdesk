from __future__ import annotations

import argparse
import getpass
import os
import platform
import socket
import sys
import time
from collections.abc import Sequence

import httpx

from auto_backup_client.baidu.auth_workflow import BaiduAuthWorkflow, generate_ephemeral_device_name
from auto_backup_client.baidu.cloud_api import BaiduCloudClient, CloudAPIError, register_device


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="使用真实云端 API 联调百度授权链路。")
    parser.add_argument("--base-url", default=os.environ.get("CLOUD_API_BASE_URL", "https://backup.baichengedu.com"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("health", help="检查真实云端 healthz/readyz。")

    accounts_parser = subparsers.add_parser("accounts", help="读取真实云端百度账号列表。")
    _add_token_args(accounts_parser)

    select_parser = subparsers.add_parser("select", help="选择已有百度账号。")
    _add_token_args(select_parser)
    select_parser.add_argument("account_id")

    device_parser = subparsers.add_parser("device-code", help="启动真实设备码授权并等待完成。")
    _add_token_args(device_parser)
    device_parser.add_argument("--register-ephemeral-device", action="store_true", help="无 Device Token 时注册临时设备。")
    device_parser.add_argument("--device-name", default="", help="注册临时设备时使用的设备名。")
    device_parser.add_argument("--password-env", default="", help="从指定环境变量读取授权密码。")
    device_parser.add_argument("--poll-seconds", type=int, default=5)
    device_parser.add_argument("--timeout-seconds", type=int, default=600)

    args = parser.parse_args(argv)
    if args.command == "health":
        return _health(args.base_url)
    if args.command == "accounts":
        return _accounts(args.base_url, _resolve_device_token(args))
    if args.command == "select":
        return _select(args.base_url, _resolve_device_token(args), args.account_id)
    if args.command == "device-code":
        token = _resolve_device_token(args, allow_empty=True)
        if not token and args.register_ephemeral_device:
            token = _register_ephemeral(args.base_url, args.device_name)
        if not token:
            raise SystemExit("CLOUD_API_DEVICE_TOKEN is required unless --register-ephemeral-device is used")
        return _device_code(
            args.base_url,
            token,
            password_env=args.password_env,
            poll_seconds=args.poll_seconds,
            timeout_seconds=args.timeout_seconds,
        )
    return 2


def _add_token_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--device-token-env",
        default="CLOUD_API_DEVICE_TOKEN",
        help="读取 Device Token 的环境变量名，默认 CLOUD_API_DEVICE_TOKEN。",
    )


def _resolve_device_token(args: argparse.Namespace, *, allow_empty: bool = False) -> str:
    token = os.environ.get(args.device_token_env, "").strip()
    if not token and not allow_empty:
        raise SystemExit(f"{args.device_token_env} is required")
    return token


def _health(base_url: str) -> int:
    with httpx.Client(timeout=10.0) as client:
        for path in ("/v1/healthz", "/v1/readyz"):
            response = client.get(base_url.rstrip("/") + path)
            _print(f"{path}: HTTP {response.status_code}")
            if response.status_code >= 400:
                _print(response.text)
                return 1
    return 0


def _accounts(base_url: str, device_token: str) -> int:
    with BaiduCloudClient(base_url, device_token) as cloud:
        accounts = cloud.list_accounts()
    _print(f"真实云端账号数量: {len(accounts)}")
    for account in accounts:
        marker = "*" if account.selected else " "
        _print(
            f"{marker} {account.account_id} | {account.display_name or account.baidu_uid} | "
            f"token_valid={account.token_valid} | version={account.token_version} | verify={account.last_verify_status}"
        )
    return 0


def _select(base_url: str, device_token: str, account_id: str) -> int:
    with BaiduCloudClient(base_url, device_token) as cloud:
        account = cloud.select_account(account_id)
    _print(f"已选择账号: {account.account_id} | {account.display_name or account.baidu_uid}")
    return 0


def _register_ephemeral(base_url: str, device_name: str) -> str:
    registration = register_device(
        base_url,
        device_name=device_name.strip() or generate_ephemeral_device_name(),
        hostname=socket.gethostname(),
        os_version=platform.platform(),
        client_version="0.1.0",
    )
    _print(f"已注册临时设备: {registration.device_id}")
    _print("Device Token 只在当前进程内使用，脚本不会写入文件。")
    return registration.device_token


def _device_code(
    base_url: str,
    device_token: str,
    *,
    password_env: str,
    poll_seconds: int,
    timeout_seconds: int,
) -> int:
    password = os.environ.get(password_env, "") if password_env else ""
    if not password:
        password = getpass.getpass("授权密码（不回显，不写入文件）: ")
    if not password:
        raise SystemExit("authorization password is required")

    poll_seconds = max(2, poll_seconds)
    deadline = time.monotonic() + max(30, timeout_seconds)
    with BaiduCloudClient(base_url, device_token, timeout=30.0) as cloud:
        workflow = BaiduAuthWorkflow(cloud)
        state = workflow.start_device_code_session()
        session = state.session
        _print("已创建真实云端设备码授权 session。")
        _print(f"session_id: {session.session_id}")
        _print(f"user_code: {session.user_code}")
        _print(f"verification_url: {session.verification_url}")
        if session.qrcode_url:
            _print(f"qrcode_url: {session.qrcode_url}")
        _print("请在百度官方授权页完成授权。脚本会持续尝试完成 token 加密入库。")

        last_message = ""
        while time.monotonic() < deadline:
            try:
                result, _material = workflow.complete_password_session(
                    session.session_id,
                    authorization_password=password,
                )
            except CloudAPIError as exc:
                if exc.status_code == 409 and exc.error_code == "authorization_pending":
                    message = "authorization_pending"
                    if message != last_message:
                        _print("等待百度授权完成...")
                        last_message = message
                    time.sleep(poll_seconds)
                    continue
                _print(f"真实授权链路失败: HTTP {exc.status_code} {exc.error_code}: {exc.message}")
                return 1

            _print("授权完成，云端已保存密文 token。")
            _print(f"account_id: {result.account.account_id}")
            _print(f"display_name: {result.account.display_name or result.account.baidu_uid}")
            _print(f"token_version: {result.account.token_version}")
            _print(f"selected: {result.account.selected}")
            return 0

    _print("等待授权超时，未写入新的账号 token。")
    return 1


def _print(message: str) -> None:
    print(message, flush=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
