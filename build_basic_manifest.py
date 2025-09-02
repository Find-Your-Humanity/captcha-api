import os
import sys
import json
from typing import List, Set
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


def _list_keys_v2(prefix: str, max_keys: int, allowed_exts: Set[str]) -> List[str]:
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
			k = key.lower()
			if allowed_exts:
				ok = any(k.endswith(ext) for ext in allowed_exts)
				if not ok:
					continue
			keys.append(key)
			if len(keys) >= max_keys:
				return keys
		if not resp.get("IsTruncated"):
			break
		token = resp.get("NextContinuationToken")
	return keys


def _save_basic_manifest(keys: List[str], verbose: bool = False) -> None:
	mongo_uri = _env("MONGO_URI", _env("MONGO_URL"))
	mongo_db = _env("MONGO_DB")
	mongo_col = _env("BASIC_MANIFEST_COLLECTION", "basic_manifest")
	if not (mongo_uri and mongo_db and mongo_col):
		raise RuntimeError("Missing Mongo envs: MONGO_URI/URL, MONGO_DB, BASIC_MANIFEST_COLLECTION")
	try:
		client = _build_mongo_client(verbose=verbose)
	except Exception as e:
		raise RuntimeError(str(e))
	try:
		coll = client[mongo_db][mongo_col]
		doc_id = "basic:all"
		payload = {
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


def main() -> None:
	parser = argparse.ArgumentParser(description="Build basic_manifest from object storage")
	parser.add_argument("--env", choices=["production", "development"], help="Select .env.<env> to load", required=False)
	parser.add_argument("--env-file", help="Explicit path to env file to load", required=False)
	parser.add_argument("--prefix", help="Object storage prefix to scan (e.g., realcatcha-cdn/images/dataset/basic/)", required=False)
	parser.add_argument("--max-keys", type=int, default=int(os.getenv("BASIC_MAX_KEYS", "5000")), help="Max keys to collect")
	parser.add_argument("--verbose", action="store_true", help="Enable verbose logs")
	parser.add_argument("--test-conn", action="store_true", help="Only test Mongo connectivity and exit")
	args = parser.parse_args()

	if load_dotenv is not None:
		try:
			load_dotenv(dotenv_path=Path("/app/.env"))
			load_dotenv()
			if args.env_file:
				load_dotenv(dotenv_path=Path(args.env_file), override=True)
			else:
				env_name = args.env or (os.getenv("APP_ENV", "").lower() or None)
				if env_name in ("production", "development"):
					load_dotenv(dotenv_path=Path(f".env.{env_name}"), override=True)
		except Exception:
			pass

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

	allowed_exts: Set[str] = set([".jpg"])
	prefix = args.prefix or os.getenv("BASIC_PREFIX", "images/dataset/basic/")
	max_keys = int(args.max_keys)
	if args.verbose:
		print(f"[basic] listing keys from prefix='{prefix}' max={max_keys}")
	keys = _list_keys_v2(prefix, max_keys, allowed_exts)
	# 중복 제거
	seen = set()
	uniq: List[str] = []
	for k in keys:
		if k in seen:
			continue
		seen.add(k)
		uniq.append(k)
		if len(uniq) >= max_keys:
			break
	if not uniq:
		print("skip: no keys found under prefix")
		return
	_save_basic_manifest(uniq, verbose=args.verbose)
	print(f"saved basic_manifest keys={len(uniq)}")


if __name__ == "__main__":
	try:
		main()
	except Exception as e:
		print(f"error: {e}")
		sys.exit(1)
