import os
import sys
import json
from typing import Dict, List, Set
from pathlib import Path
import argparse
from datetime import datetime, timezone
try:
    from dotenv import load_dotenv  # type: ignore
except Exception:
    load_dotenv = None  # optional


def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return v if v is not None else default


def _mask_uri(uri: str) -> str:
    # hide password if present: scheme://user:pass@host → scheme://user:***@host
    try:
        if "@" in uri and "://" in uri:
            scheme, rest = uri.split("://", 1)
            creds, host = rest.split("@", 1)
            if ":" in creds:
                user, _ = creds.split(":", 1)
                return f"{scheme}://{user}:***@{host}"
    except Exception:
        pass
    return uri


def _build_mongo_client(verbose: bool = False):
    mongo_uri = _env("MONGO_URI", _env("MONGO_URL"))
    if not mongo_uri:
        raise RuntimeError("Missing Mongo URI: set MONGO_URI or MONGO_URL")
    # timeouts
    sel_to = int(_env("MONGO_SERVER_SELECTION_TIMEOUT_MS", "30000"))
    conn_to = int(_env("MONGO_CONNECT_TIMEOUT_MS", "20000"))
    sock_to = int(_env("MONGO_SOCKET_TIMEOUT_MS", "20000"))
    if verbose:
        print(f"[mongo] uri={_mask_uri(mongo_uri)} sel_to={sel_to}ms conn_to={conn_to}ms sock_to={sock_to}ms")
    from pymongo import MongoClient  # type: ignore
    client = MongoClient(
        mongo_uri,
        serverSelectionTimeoutMS=sel_to,
        connectTimeoutMS=conn_to,
        socketTimeoutMS=sock_to,
    )
    return client


def _list_keys_v2(prefix: str, max_per_class: int, allowed_exts: Set[str]) -> List[str]:
    endpoint = _env("OBJECT_STORAGE_ENDPOINT")
    region = _env("OBJECT_STORAGE_REGION", "kr-central-2")
    bucket = _env("OBJECT_STORAGE_BUCKET")
    ak = _env("OBJECT_STORAGE_ACCESS_KEY")
    sk = _env("OBJECT_STORAGE_SECRET_KEY")
    if not (endpoint and bucket and ak and sk):
        raise RuntimeError("Missing object storage envs: endpoint/bucket/access/secret")

    try:
        import boto3  # type: ignore
    except Exception as e:
        raise RuntimeError(f"boto3 not installed: {e}")

    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=ak,
        aws_secret_access_key=sk,
    )
    keys: List[str] = []
    token = None
    while True:
        params = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
        if token:
            params["ContinuationToken"] = token
        resp = s3.list_objects_v2(**params)
        for item in resp.get("Contents", []):
            key = item.get("Key")
            if not key or key.endswith("/"):
                continue
            # 확장자 필터
            k = key.lower()
            if allowed_exts:
                matched = False
                for ext in allowed_exts:
                    if k.endswith(ext):
                        matched = True
                        break
                if not matched:
                    continue
            keys.append(key)
            if len(keys) >= max_per_class:
                return keys
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
    return keys


def _load_prefix_map_from_mongo(verbose: bool = False) -> Dict[str, List[str]]:
    mongo_uri = _env("MONGO_URI", _env("MONGO_URL"))
    mongo_db = _env("MONGO_DB")
    # 프리픽스(클래스 메타) 전용 컬렉션
    mongo_col = _env("MONGO_PREFIX_COLLECTION", _env("MONGO_COLLECTION"))
    if not (mongo_uri and mongo_db and mongo_col):
        raise RuntimeError("Missing Mongo envs: MONGO_URI/URL, MONGO_DB, MONGO_COLLECTION")
    try:
        client = _build_mongo_client(verbose=verbose)
    except Exception as e:
        raise RuntimeError(str(e))
    try:
        coll = client[mongo_db][mongo_col]
        # optional connectivity check
        if verbose:
            try:
                client.admin.command("ping")
                print("[mongo] ping ok")
            except Exception as pe:
                print(f"[mongo] ping failed: {pe}")
        mapping: Dict[str, List[str]] = {}
        for d in coll.find({}, {"name": 1, "cdn_prefix": 1}):
            cls = str(d.get("name") or "").strip()
            prefix = str(d.get("cdn_prefix") or "").strip()
            if not cls or not prefix:
                continue
            mapping.setdefault(cls, []).append(prefix)
        return mapping
    finally:
        try:
            client.close()
        except Exception:
            pass


def _save_class_manifest(cls: str, prefixes: List[str], keys: List[str], verbose: bool = False) -> None:
    mongo_uri = _env("MONGO_URI", _env("MONGO_URL"))
    mongo_db = _env("MONGO_DB")
    # 매니페스트 저장 전용 컬렉션(분리)
    mongo_col = _env("MONGO_MANIFEST_COLLECTION", _env("MONGO_COLLECTION", "abstract_manifest") + "_manifest")
    if not (mongo_uri and mongo_db and mongo_col):
        raise RuntimeError("Missing Mongo envs: MONGO_URI/URL, MONGO_DB, MONGO_MANIFEST_COLLECTION")
    try:
        client = _build_mongo_client(verbose=verbose)
    except Exception as e:
        raise RuntimeError(str(e))
    try:
        coll = client[mongo_db][mongo_col]
        doc_id = f"manifest:{cls}"
        payload = {
            "class": cls,
            "prefixes": prefixes,
            "keys": keys,
            "count": len(keys),
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
        coll.update_one({"_id": doc_id}, {"$set": payload}, upsert=True)
    finally:
        try:
            client.close()
        except Exception:
            pass


def _cleanup_stale(valid_classes: List[str], verbose: bool = False) -> None:
    mongo_uri = _env("MONGO_URI", _env("MONGO_URL"))
    mongo_db = _env("MONGO_DB")
    mongo_col = _env("MONGO_MANIFEST_COLLECTION", _env("MONGO_COLLECTION", "abstract_manifest") + "_manifest")
    if not (mongo_uri and mongo_db and mongo_col):
        return
    clean = _env("MANIFEST_CLEAN", "false").lower() == "true"
    if not clean:
        return
    try:
        client = _build_mongo_client(verbose=verbose)
    except Exception:
        return
    try:
        coll = client[mongo_db][mongo_col]
        coll.delete_many({"class": {"$nin": valid_classes}})
    finally:
        try:
            client.close()
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Build class→file keys manifest into Mongo")
    parser.add_argument("--env", choices=["production", "development"], help="Select .env.<env> to load", required=False)
    parser.add_argument("--env-file", help="Explicit path to env file to load", required=False)
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logs")
    parser.add_argument("--test-conn", action="store_true", help="Only test Mongo connectivity and exit")
    args = parser.parse_args()

    # Load env from files if available (container and local dev)
    if load_dotenv is not None:
        try:
            # base envs
            load_dotenv(dotenv_path=Path("/app/.env"))
            load_dotenv()
            # explicit overrides
            if args.env_file:
                load_dotenv(dotenv_path=Path(args.env_file), override=True)
            else:
                env_name = args.env or (os.getenv("APP_ENV", "").lower() or None)
                if env_name in ("production", "development"):
                    load_dotenv(dotenv_path=Path(f".env.{env_name}"), override=True)
        except Exception:
            pass
    max_per_class = int(_env("MANIFEST_MAX_PER_CLASS", "2000"))
    allowed_exts: Set[str] = set([".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"]) 
    if args.test_conn:
        try:
            c = _build_mongo_client(verbose=args.verbose)
            c.admin.command("ping")
            print("[mongo] connectivity OK")
            c.close()
        except Exception as e:
            print(f"[mongo] connectivity FAILED: {e}")
            sys.exit(1)
        sys.exit(0)

    prefix_map = _load_prefix_map_from_mongo(verbose=args.verbose)
    processed_classes: List[str] = []
    for cls, prefixes in prefix_map.items():
        all_keys: List[str] = []
        for prefix in prefixes:
            try:
                keys = _list_keys_v2(prefix, max_per_class=max_per_class, allowed_exts=allowed_exts)
                all_keys.extend(keys)
            except Exception as e:
                print(f"warn: list failed for class='{cls}' prefix='{prefix}': {e}")
        # 중복 제거 및 제한 적용
        seen = set()
        uniq: List[str] = []
        for k in all_keys:
            if k in seen:
                continue
            seen.add(k)
            uniq.append(k)
            if len(uniq) >= max_per_class:
                break
        if not uniq:
            print(f"skip: no keys for class='{cls}'")
            continue
        _save_class_manifest(cls, prefixes, uniq, verbose=args.verbose)
        processed_classes.append(cls)
        print(f"saved class='{cls}' keys={len(uniq)}")

    _cleanup_stale(processed_classes, verbose=args.verbose)
    print(f"saved manifests classes={len(processed_classes)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"error: {e}")
        sys.exit(1)


