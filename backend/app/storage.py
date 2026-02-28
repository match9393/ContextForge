from botocore.client import Config
import boto3

from app.config import settings


def get_s3_client(*, endpoint_url: str | None = None):
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url or settings.s3_endpoint,
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


def download_bytes(*, bucket_name: str, key: str) -> bytes:
    s3 = get_s3_client()
    response = s3.get_object(Bucket=bucket_name, Key=key)
    body = response.get("Body")
    if body is None:
        return b""
    return body.read()


def generate_presigned_get_url(*, bucket_name: str, key: str, expires_seconds: int = 3600) -> str:
    public_endpoint = settings.s3_public_endpoint.strip() or settings.s3_endpoint
    s3 = get_s3_client(endpoint_url=public_endpoint)
    return s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": bucket_name, "Key": key},
        ExpiresIn=expires_seconds,
    )


def delete_prefix(*, bucket_name: str, prefix: str) -> int:
    s3 = get_s3_client()
    deleted = 0
    continuation_token: str | None = None

    while True:
        list_kwargs = {"Bucket": bucket_name, "Prefix": prefix}
        if continuation_token:
            list_kwargs["ContinuationToken"] = continuation_token
        result = s3.list_objects_v2(**list_kwargs)

        contents = result.get("Contents", [])
        keys = [{"Key": item["Key"]} for item in contents]
        if keys:
            for i in range(0, len(keys), 1000):
                batch = keys[i : i + 1000]
                s3.delete_objects(Bucket=bucket_name, Delete={"Objects": batch, "Quiet": True})
                deleted += len(batch)

        if not result.get("IsTruncated"):
            break
        continuation_token = result.get("NextContinuationToken")

    return deleted
