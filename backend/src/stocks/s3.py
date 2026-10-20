import hashlib

import boto3
from botocore.client import Config

from stocks.settings import Settings


def store_snapshot(
    config: Settings, source_id: str, payload: bytes, *, content_type: str
) -> tuple[str, str]:
    if not config.s3_endpoint_url:
        raise RuntimeError("S3_ENDPOINT_URL must be configured to collect data")
    if not config.s3_bucket.strip():
        raise RuntimeError("S3_BUCKET must be configured for snapshot storage")
    if bool(config.s3_access_key) != bool(config.s3_secret_key):
        raise RuntimeError(
            "S3_ACCESS_KEY and S3_SECRET_KEY must be configured together"
        )
    if not config.s3_access_key and not config.s3_secret_key:
        raise RuntimeError(
            "S3_ACCESS_KEY and S3_SECRET_KEY must be configured for snapshot storage"
        )
    digest = hashlib.sha256(payload).hexdigest()
    key = f"sources/{source_id}/{digest}"
    options = {
        "endpoint_url": config.s3_endpoint_url,
        "region_name": config.s3_region,
        "config": Config(signature_version="s3v4"),
    }
    if config.s3_access_key and config.s3_secret_key:
        options.update(
            aws_access_key_id=config.s3_access_key,
            aws_secret_access_key=config.s3_secret_key,
        )
    client = boto3.client("s3", **options)
    client.put_object(
        Bucket=config.s3_bucket, Key=key, Body=payload, ContentType=content_type
    )
    return key, digest


def read_snapshot(config: Settings, object_key: str) -> bytes:
    if not config.s3_endpoint_url:
        raise RuntimeError("S3_ENDPOINT_URL must be configured to collect data")
    if not config.s3_bucket.strip():
        raise RuntimeError("S3_BUCKET must be configured for snapshot storage")
    if bool(config.s3_access_key) != bool(config.s3_secret_key):
        raise RuntimeError(
            "S3_ACCESS_KEY and S3_SECRET_KEY must be configured together"
        )
    if not config.s3_access_key and not config.s3_secret_key:
        raise RuntimeError(
            "S3_ACCESS_KEY and S3_SECRET_KEY must be configured for snapshot storage"
        )
    options = {
        "endpoint_url": config.s3_endpoint_url,
        "region_name": config.s3_region,
        "config": Config(signature_version="s3v4"),
    }
    if config.s3_access_key and config.s3_secret_key:
        options.update(
            aws_access_key_id=config.s3_access_key,
            aws_secret_access_key=config.s3_secret_key,
        )
    client = boto3.client("s3", **options)
    response = client.get_object(Bucket=config.s3_bucket, Key=object_key)
    return response["Body"].read()
