from pathlib import Path

import boto3
import pytest
from botocore.stub import ANY, Stubber

import publish


def test_make_r2_client_missing_env_raises(monkeypatch):
    for var in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(publish.UploadError, match="R2_ACCOUNT_ID"):
        publish.make_r2_client()


def test_make_r2_client_uses_account_endpoint(monkeypatch):
    monkeypatch.setenv("R2_ACCOUNT_ID", "acct123")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "secret")
    client = publish.make_r2_client()
    assert client.meta.endpoint_url == "https://acct123.r2.cloudflarestorage.com"


def test_upload_file_puts_object(tmp_path):
    f = tmp_path / "a.mp3"
    f.write_bytes(b"mp3bytes")
    client = boto3.client(
        "s3", region_name="auto", endpoint_url="https://x.r2.cloudflarestorage.com",
        aws_access_key_id="k", aws_secret_access_key="s",
    )
    with Stubber(client) as stub:
        stub.add_response(
            "put_object", {},
            {"Bucket": "agent-fm", "Key": "episodes/a.mp3",
             "Body": ANY, "ContentType": "audio/mpeg"},
        )
        publish.upload_file(client, "agent-fm", "episodes/a.mp3", f, "audio/mpeg")
        stub.assert_no_pending_responses()


def test_upload_file_wraps_missing_file(tmp_path):
    missing = tmp_path / "gone.mp3"
    client = boto3.client(
        "s3", region_name="auto", endpoint_url="https://x.r2.cloudflarestorage.com",
        aws_access_key_id="k", aws_secret_access_key="s",
    )
    with Stubber(client):  # no queued responses: put_object must never be reached
        with pytest.raises(publish.UploadError, match="gone.mp3"):
            publish.upload_file(
                client, "agent-fm", "episodes/a.mp3", missing, "audio/mpeg"
            )


def test_upload_file_wraps_client_errors(tmp_path):
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x")
    client = boto3.client(
        "s3", region_name="auto", endpoint_url="https://x.r2.cloudflarestorage.com",
        aws_access_key_id="k", aws_secret_access_key="s",
    )
    with Stubber(client) as stub:
        stub.add_client_error("put_object", service_error_code="AccessDenied")
        with pytest.raises(publish.UploadError):
            publish.upload_file(client, "agent-fm", "episodes/a.mp3", f, "audio/mpeg")
