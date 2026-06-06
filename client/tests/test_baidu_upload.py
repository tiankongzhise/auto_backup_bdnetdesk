from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from types import SimpleNamespace

from auto_backup_client.baidu import upload_cli
from auto_backup_client.baidu.upload import (
    DEFAULT_PART_SIZE,
    BaiduNetdiskError,
    BaiduQuota,
    PrecreateResult,
    build_archive_remote_path,
    compute_file_block_plan,
    normalize_backup_root_dir,
)
from auto_backup_client.baidu.metadata import StableJsonDocument


def test_build_archive_remote_path_uses_required_date_device_job_layout() -> None:
    remote_path = build_archive_remote_path(
        root_dir="/apps/auto_backup_bdnetdesk/backups",
        job_created_at=datetime(2026, 6, 5, 7, 8, tzinfo=timezone.utc),
        device_id="dev_001",
        job_id="job-abc",
        archive_seq=1,
        archive_sha256="a" * 64,
    )

    assert remote_path == (
        "/apps/auto_backup_bdnetdesk/backups/2026/06/05/"
        "dev_001/job-abc/archives/000001-" + ("a" * 64) + ".7z"
    )


def test_backup_root_must_stay_under_apps() -> None:
    try:
        normalize_backup_root_dir("/not-apps/auto_backup_bdnetdesk/backups")
    except ValueError as exc:
        assert "/apps/{appname}" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_compute_file_block_plan_uses_ordered_part_md5s(tmp_path) -> None:
    local_file = tmp_path / "archive.7z"
    local_file.write_bytes((b"a" * DEFAULT_PART_SIZE) + b"tail")

    plan = compute_file_block_plan(local_file)

    assert plan.size == DEFAULT_PART_SIZE + 4
    assert len(plan.parts) == 2
    assert plan.block_md5s == (
        hashlib.md5(b"a" * DEFAULT_PART_SIZE).hexdigest(),
        hashlib.md5(b"tail").hexdigest(),
    )
    assert plan.content_md5 == hashlib.md5((b"a" * DEFAULT_PART_SIZE) + b"tail").hexdigest()
    assert plan.slice_md5 == hashlib.md5(b"a" * (256 * 1024)).hexdigest()


def test_empty_precreate_block_list_means_no_missing_parts() -> None:
    result = PrecreateResult(path="/apps/app/file.7z", uploadid="upload-1", return_type=1, block_list=tuple())

    assert result.partseqs_to_upload(total_parts=1) == tuple()
    assert result.partseqs_to_upload(total_parts=3) == tuple()


def test_precreate_block_list_is_sorted_for_resume() -> None:
    result = PrecreateResult(path="/apps/app/file.7z", uploadid="upload-1", return_type=1, block_list=(2, 0))

    assert result.partseqs_to_upload(total_parts=3) == (0, 2)


def test_real_batch_does_not_require_user_info_probe(monkeypatch, tmp_path, capsys) -> None:
    class FakeBaiduNetdiskClient:
        uploaded_paths: set[str] = set()

        def __init__(self, access_token: str, **_kwargs: object) -> None:
            assert access_token == "access-token"

        def __enter__(self) -> "FakeBaiduNetdiskClient":
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def get_quota(self) -> BaiduQuota:
            return BaiduQuota(total=DEFAULT_PART_SIZE * 4, used=0)

        def get_user_info(self, **_kwargs: object) -> object:
            raise AssertionError("real-batch must not call the uinfo probe")

        def upload_file_complete(self, *, local_path, remote_path: str, part_size: int, rtype: int) -> object:
            del rtype
            if remote_path in self.uploaded_paths:
                raise BaiduNetdiskError("conflict", error_code="31034")
            self.uploaded_paths.add(remote_path)
            plan = compute_file_block_plan(local_path, part_size=part_size)
            return SimpleNamespace(
                remote_path=remote_path,
                plan=plan,
                uploaded_parts=tuple(SimpleNamespace(partseq=part.partseq) for part in plan.parts),
                created=SimpleNamespace(fs_id=len(self.uploaded_paths)),
            )

        def delete_files(self, remote_paths, *, async_mode: int) -> object:
            assert async_mode == 0
            assert tuple(remote_paths)
            return SimpleNamespace(errno=0)

    class FakeCloudClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> "FakeCloudClient":
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

    monkeypatch.setattr(upload_cli, "_read_authorization_password", lambda _env: "password")
    monkeypatch.setattr(upload_cli, "_resolve_credentials", lambda _args: (SimpleNamespace(device_id="dev-test", device_token="device-token"), "test"))
    monkeypatch.setattr(
        upload_cli,
        "_decrypt_selected_token",
        lambda *_args: SimpleNamespace(
            token=SimpleNamespace(access_token="access-token"),
            encrypted=SimpleNamespace(account_id="account-1", token_version=3),
        ),
    )
    monkeypatch.setattr(upload_cli, "BaiduCloudClient", FakeCloudClient)
    monkeypatch.setattr(upload_cli, "BaiduNetdiskClient", FakeBaiduNetdiskClient)

    args = SimpleNamespace(
        base_url="https://backup.example.test",
        password_env="",
        device_token_env="",
        account_id="",
        root_dir="/apps/auto_backup_bdnetdesk/backups",
        device_id="",
        job_id="job-test",
        work_dir=str(tmp_path),
        keep_remote=False,
    )

    assert upload_cli._real_batch(args) == 0
    output = capsys.readouterr().out

    assert "uinfo_error_code" not in output
    assert "真实上传批测完成: quota -> small upload -> multipart upload -> conflict" in output


def test_upload_resumable_output_does_not_expose_tokens_or_local_paths(monkeypatch, tmp_path, capsys) -> None:
    local_archive = tmp_path / "secret-name.7z"
    local_archive.write_bytes(b"archive")
    sqlite_path = tmp_path / "backup_state.sqlite3"

    class FakeBaiduNetdiskClient:
        def __init__(self, access_token: str, **_kwargs: object) -> None:
            assert access_token == "access-token-secret"

        def __enter__(self) -> "FakeBaiduNetdiskClient":
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

    class FakeCloudClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> "FakeCloudClient":
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

    class FakeUploader:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def upload(self, value):
            assert value.local_path == local_archive
            return SimpleNamespace(
                upload_session_id="upload-session-1",
                archive_sha256="a" * 64,
                remote_archive_path="/apps/app/backups/2026/06/06/device-1/job-1/archives/000001-" + ("a" * 64) + ".7z",
                remote_meta_path="/apps/app/backups/2026/06/06/device-1/job-1/archives/000001-" + ("a" * 64) + ".meta.json",
                remote_job_index_path="/apps/app/backups/2026/06/06/device-1/job-1/job.index.json",
                reused_uploadid=True,
                uploaded_partseqs=(1,),
                created=SimpleNamespace(fs_id=101),
                meta_created=SimpleNamespace(fs_id=102),
                job_index_created=SimpleNamespace(fs_id=103),
                archive_meta=StableJsonDocument(data={}, text="{}", sha256="b" * 64),
                job_index=StableJsonDocument(data={}, text="{}", sha256="c" * 64),
            )

    monkeypatch.setattr(upload_cli, "_read_authorization_password", lambda _env: "password-secret")
    monkeypatch.setattr(upload_cli, "_resolve_credentials", lambda _args: (SimpleNamespace(device_id="device-1", device_token="device-token-secret"), "本机 DPAPI 凭据"))
    monkeypatch.setattr(
        upload_cli,
        "_decrypt_selected_token",
        lambda *_args: SimpleNamespace(
            token=SimpleNamespace(access_token="access-token-secret"),
            encrypted=SimpleNamespace(account_id="account-1", token_version=7),
        ),
    )
    monkeypatch.setattr(upload_cli, "BaiduCloudClient", FakeCloudClient)
    monkeypatch.setattr(upload_cli, "BaiduNetdiskClient", FakeBaiduNetdiskClient)
    monkeypatch.setattr(upload_cli, "BaiduResumableUploader", FakeUploader)

    args = SimpleNamespace(
        base_url="https://backup.example.test",
        password_env="",
        device_token_env="",
        account_id="",
        local_path=str(local_archive),
        root_dir="/apps/auto_backup_bdnetdesk/backups",
        device_id="",
        job_id="job-1",
        archive_seq=1,
        archive_type="payload",
        manifest_id="",
        part_size_mib=4,
        sqlite_path=str(sqlite_path),
        check_quota=False,
    )

    assert upload_cli._upload_resumable(args) == 0
    output = capsys.readouterr().out

    assert str(local_archive) not in output
    assert "secret-name.7z" not in output
    assert "device-token-secret" not in output
    assert "access-token-secret" not in output
    assert "password-secret" not in output
    assert "remote_archive_path_sha256:" in output
    assert "可恢复上传完成" in output
