from typing import Optional, Callable


def build_cdn_url(
    path_or_key: str,
    is_remote: bool,
    *,
    asset_base_url: Optional[str],
    map_local_to_key: Optional[Callable[[str], Optional[str]]] = None,
) -> Optional[str]:
    """Pure helper to build a CDN URL.

    This utility is decoupled from main to avoid circular imports.
    Callers must provide `asset_base_url` and, when is_remote is False,
    a `map_local_to_key` function that converts a local path to an object key.
    """
    if not asset_base_url:
        return None
    if is_remote:
        key_like = str(path_or_key).lstrip("/")
    else:
        if map_local_to_key is None:
            return None
        mapped = map_local_to_key(str(path_or_key))
        if not mapped:
            return None
        key_like = mapped.lstrip("/")
    return f"{asset_base_url.rstrip('/')}/{key_like}"



def presign_url_for_key(key: str) -> Optional[str]:
    from config.settings import (
        ENV,
        OBJECT_STORAGE_BUCKET,
        OBJECT_STORAGE_ENDPOINT,
        OBJECT_STORAGE_REGION,
        OBJECT_STORAGE_ACCESS_KEY,
        OBJECT_STORAGE_SECRET_KEY,
        PRESIGN_TTL_SECONDS,
    )
    if ENV != "production":
        return None
    if not (OBJECT_STORAGE_BUCKET and OBJECT_STORAGE_ENDPOINT and OBJECT_STORAGE_ACCESS_KEY and OBJECT_STORAGE_SECRET_KEY):
        return None
    try:
        import boto3  # type: ignore
        s3 = boto3.client(
            "s3",
            endpoint_url=OBJECT_STORAGE_ENDPOINT,
            region_name=OBJECT_STORAGE_REGION,
            aws_access_key_id=OBJECT_STORAGE_ACCESS_KEY,
            aws_secret_access_key=OBJECT_STORAGE_SECRET_KEY,
        )
        return s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": OBJECT_STORAGE_BUCKET, "Key": key},
            ExpiresIn=PRESIGN_TTL_SECONDS,
            HttpMethod="GET",
        )
    except Exception as e:
        try:
            print(f"⚠️ presign failed: {e}")
        except Exception:
            pass
        return None
