import json as _json
import time
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

def create_checkbox_session(session_id: str, ttl: int = 300) -> bool:
    """
    체크박스 세션을 생성합니다.
    
    Args:
        session_id: 세션 ID
        ttl: 세션 만료 시간 (초, 기본값: 5분)
    
    Returns:
        bool: 생성 성공 여부
    """
    r = get_redis()
    if not r:
        return False
    
    try:
        session_data = {
            "session_id": session_id,
            "attempts": 0,
            "low_score_attempts": 0,  # confidence_score 9 이하 시도 횟수
            "is_blocked": False,
            "created_at": time.time(),
            "last_attempt_at": None
        }
        key = rkey("checkbox_session", session_id)
        return redis_set_json(key, session_data, ttl)
    except Exception:
        return False


def get_checkbox_session(session_id: str) -> dict:
    """
    체크박스 세션을 조회합니다.
    
    Args:
        session_id: 세션 ID
    
    Returns:
        dict: 세션 데이터 또는 None
    """
    if not session_id:
        return None
    
    key = rkey("checkbox_session", session_id)
    return redis_get_json(key)


def increment_checkbox_attempts(session_id: str, is_bot_suspected: bool = False, ttl: int = 300) -> dict:
    """
    체크박스 시도 횟수를 증가시킵니다.
    
    Args:
        session_id: 세션 ID
        is_bot_suspected: 봇으로 의심되는 시도 여부 (confidence_score >= 91)
        ttl: 세션 만료 시간 (초)
    
    Returns:
        dict: 업데이트된 세션 데이터 (status, is_disabled 포함)
    """
    r = get_redis()
    if not r:
        return {"status": "error", "is_disabled": False}
    
    try:
        key = rkey("checkbox_session", session_id)
        session_data = redis_get_json(key) or {}
        
        # 전체 시도 횟수 증가
        session_data["attempts"] = session_data.get("attempts", 0) + 1
        session_data["last_attempt_at"] = time.time()
        
        # 봇 의심 시도 처리
        if is_bot_suspected:
            session_data["bot_attempts"] = session_data.get("bot_attempts", 0) + 1
            
            # 3번 시도 후 완전 차단
            if session_data["bot_attempts"] >= 3:
                session_data["is_blocked"] = True
                session_data["status"] = "blocked"
            else:
                session_data["status"] = "bot_suspected"
        else:
            # 정상 시도
            session_data["status"] = "success"
        
        # Redis에 저장
        redis_set_json(key, session_data, ttl)
        
        # 응답 데이터 구성 (민감한 정보 제외)
        return {
            "status": session_data.get("status", "success"),
            "is_disabled": session_data.get("is_blocked", False),
            "is_blocked": session_data.get("is_blocked", False)
        }
    except Exception:
        return {"status": "error", "is_disabled": False}


def is_checkbox_session_blocked(session_id: str) -> bool:
    """
    체크박스 세션이 차단되었는지 확인합니다.
    
    Args:
        session_id: 세션 ID
    
    Returns:
        bool: 차단 여부
    """
    session_data = get_checkbox_session(session_id)
    if not session_data:
        return False
    
    return session_data.get("is_blocked", False)


