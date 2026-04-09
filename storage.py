import logging
import os
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from result import Err, Ok, Result

log = logging.getLogger(__name__)

BUCKET = os.environ.get("S3_BUCKET", "kobo-converter-195950944512")
PREFIX = "processed/"

_client = None


def _s3():
    global _client
    if _client is None:
        _client = boto3.client("s3")
    return _client


def upload(local_path: Path) -> Result[str]:
    key = f"{PREFIX}{local_path.name}"
    try:
        _s3().upload_file(str(local_path), BUCKET, key)
        log.info("Uploaded %s -> s3://%s/%s", local_path.name, BUCKET, key)
        return Ok(key)
    except ClientError as e:
        log.error("S3 upload failed for %s: %s", local_path.name, e)
        return Err(f"S3 upload failed: {e}")


def list_files() -> list[str]:
    try:
        resp = _s3().list_objects_v2(Bucket=BUCKET, Prefix=PREFIX)
        return [
            obj["Key"].removeprefix(PREFIX)
            for obj in resp.get("Contents", [])
            if obj["Key"] != PREFIX
        ]
    except ClientError as e:
        log.error("S3 list failed: %s", e)
        return []


def download(filename: str):
    """Returns (streaming_body, content_length) or raises ClientError."""
    resp = _s3().get_object(Bucket=BUCKET, Key=f"{PREFIX}{filename}")
    return resp["Body"], resp["ContentLength"]


def delete(filename: str) -> None:
    try:
        _s3().delete_object(Bucket=BUCKET, Key=f"{PREFIX}{filename}")
        log.info("Deleted s3://%s/%s%s", BUCKET, PREFIX, filename)
    except ClientError as e:
        log.error("S3 delete failed for %s: %s", filename, e)
