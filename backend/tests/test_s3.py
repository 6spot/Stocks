import hashlib
from unittest.mock import Mock

import pytest
from stocks.s3 import read_snapshot, store_snapshot
from stocks.settings import Settings


@pytest.mark.parametrize(
    ("payload", "content_type"),
    [
        (b"<article>Disclosure</article>", "application/xml"),
        (b'{"revenue": 123}', "application/json"),
        (b"<html><body>Disclosure</body></html>", "text/html"),
        (b"%PDF-1.7\n", "application/pdf"),
    ],
)
def test_snapshot_preserves_content_and_type(monkeypatch, payload, content_type):
    client = Mock()
    monkeypatch.setattr("stocks.s3.boto3.client", Mock(return_value=client))
    config = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://unused/unused",
        s3_endpoint_url="https://storage.example",
        s3_bucket="evidence",
        s3_access_key="test-access",
        s3_secret_key="test-secret",
    )

    key, digest = store_snapshot(
        config, "publisher", payload, content_type=content_type
    )

    assert digest == hashlib.sha256(payload).hexdigest()
    assert key == f"sources/publisher/{digest}"
    client.put_object.assert_called_once_with(
        Bucket="evidence", Key=key, Body=payload, ContentType=content_type
    )


def test_read_snapshot_returns_stored_bytes(monkeypatch):
    client = Mock()
    client.get_object.return_value = {"Body": Mock(read=Mock(return_value=b"evidence"))}
    monkeypatch.setattr("stocks.s3.boto3.client", Mock(return_value=client))
    config = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://unused/unused",
        s3_endpoint_url="https://storage.example",
        s3_bucket="evidence",
        s3_access_key="test-access",
        s3_secret_key="test-secret",
    )

    assert read_snapshot(config, "sources/publisher/hash") == b"evidence"
    client.get_object.assert_called_once_with(
        Bucket="evidence", Key="sources/publisher/hash"
    )


@pytest.mark.parametrize(
    ("access_key", "secret_key"),
    [(None, "secret"), ("access", None), (None, None)],
)
def test_snapshot_credentials_must_be_configured_together(
    monkeypatch, access_key, secret_key
):
    client = Mock()
    factory = Mock(return_value=client)
    monkeypatch.setattr("stocks.s3.boto3.client", factory)
    config = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://unused/unused",
        s3_endpoint_url="https://storage.example",
        s3_access_key=access_key,
        s3_secret_key=secret_key,
    )

    with pytest.raises(RuntimeError, match="must be configured"):
        store_snapshot(config, "publisher", b"evidence", content_type="text/plain")

    factory.assert_not_called()


@pytest.mark.parametrize("operation", [store_snapshot, read_snapshot])
def test_snapshot_rejects_empty_bucket_before_client_creation(monkeypatch, operation):
    factory = Mock()
    monkeypatch.setattr("stocks.s3.boto3.client", factory)
    config = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://unused/unused",
        s3_endpoint_url="https://storage.example",
        s3_bucket="",
        s3_access_key="test-access",
        s3_secret_key="test-secret",
    )

    with pytest.raises(RuntimeError, match="S3_BUCKET"):
        if operation is store_snapshot:
            operation(config, "publisher", b"evidence", content_type="text/plain")
        else:
            operation(config, "sources/publisher/hash")

    factory.assert_not_called()
