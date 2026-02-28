from botocore.client import Config
import boto3

from app.config import settings


def get_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def ensure_bucket(bucket_name: str) -> None:
    s3 = get_s3_client()
    buckets = s3.list_buckets().get("Buckets", [])
    existing = {bucket["Name"] for bucket in buckets}
    if bucket_name not in existing:
        s3.create_bucket(Bucket=bucket_name)


def upload_bytes(*, bucket_name: str, key: str, data: bytes, content_type: str) -> None:
    s3 = get_s3_client()
    s3.put_object(
        Bucket=bucket_name,
        Key=key,
        Body=data,
        ContentType=content_type,
    )
