import hashlib
import boto3
from botocore.client import Config
from stocks.settings import Settings

def store_snapshot(config: Settings, source_id: str, payload: bytes) -> tuple[str, str]:
    if not config.s3_endpoint_url:
        raise RuntimeError("S3_ENDPOINT_URL must be configured to collect data")
    digest = hashlib.sha256(payload).hexdigest()
    key = f"sources/{source_id}/{digest}.xml"
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
        Bucket=config.s3_bucket, Key=key, Body=payload, ContentType="application/xml"
    )
    return key, digest
