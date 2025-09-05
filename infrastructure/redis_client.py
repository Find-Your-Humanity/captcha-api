import json as _json
from typing import Union

try:
    from redis.cluster import RedisCluster  # type: ignore
except Exception:
    RedisCluster = None  # type: ignore

from config.settings import (
    USE_REDIS,
    REDIS_HOST,
    REDIS_PORT,
    REDIS_PASSWORD,
    REDIS_SSL,
    REDIS_PREFIX,
    REDIS_TIMEOUT_MS,
)

_redis_client = None


def get_redis():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    if not USE_REDIS:
        return None
    if RedisCluster is None:
        return None
    try:
        client = RedisCluster(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD,
            ssl=REDIS_SSL,
            decode_responses=True,
            socket_connect_timeout=REDIS_TIMEOUT_MS / 1000.0,
            socket_timeout=REDIS_TIMEOUT_MS / 1000.0,
        )
        client.ping()
        _redis_client = client
        return _redis_client
    except Exception:
        _redis_client = None
        return None


def rkey(*parts: str) -> str:
    return REDIS_PREFIX + ":".join([p.strip(":") for p in parts if p])


def redis_set_json(key: str, value: dict, ttl: int):
    r = get_redis()
    if not r:
        return False
    data = _json.dumps(value, ensure_ascii=False)
    try:
        return r.setex(key, ttl, data)
    except Exception:
        return False


def redis_get_json(key: str):
    r = get_redis()
    if not r:
        return None
    try:
        data = r.get(key)
    except Exception:
        data = None
    if not data:
        return None
    try:
        return _json.loads(data)
    except Exception:
        return None


def redis_del(key: str):
    r = get_redis()
    if not r:
        return 0
    try:
        return r.delete(key)
    except Exception:
        return 0


def redis_incr_attempts(key: str, field: str = "attempts", ttl: Union[int, None] = None) -> int:
    r = get_redis()
    if not r:
        return -1
    try:
        # simple JSON get/modify/setex as in current codebase
        val = redis_get_json(key) or {}
        cur = int(val.get(field, 0)) + 1
        val[field] = cur
        if ttl is None:
            try:
                remain = r.ttl(key)
                ttl = int(remain) if isinstance(remain, int) and remain > 0 else 60
            except Exception:
                ttl = 60
        redis_set_json(key, val, ttl)
        return cur
    except Exception:
        return -1


