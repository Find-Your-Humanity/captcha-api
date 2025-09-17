from typing import Any, Dict, List, Optional
import time

from infrastructure.redis_client import rkey, get_redis, redis_set_json, redis_get_json, redis_del, redis_incr_attempts
from config.settings import CAPTCHA_TTL
import uuid, time
from state.sessions import ABSTRACT_SESSIONS, ABSTRACT_SESSIONS_LOCK


def create_abstract_captcha(image_urls: list[str], target_class: str, is_positive: list[bool], keywords: list[str]) -> Dict[str, Any]:
    challenge_id = uuid.uuid4().hex
    ttl_seconds = CAPTCHA_TTL
    if get_redis():
        doc = {
            "type": "abstract",
            "cid": challenge_id,
            "target_class": target_class,
            "keywords": keywords,
            "image_urls": list(image_urls),
            "is_positive": list(is_positive),
            "attempts": 0,
            "created_at": time.time(),
        }
        redis_set_json(rkey("abstract", challenge_id), doc, ttl_seconds)
    return {
        "challenge_id": challenge_id,
        "question": f"{keywords[0]} 이미지를 골라주세요" if keywords else "Select",
        "ttl": ttl_seconds,
        "images": [{"id": i, "url": u} for i, u in enumerate(image_urls)],
        # 🔒 보안 강화: target_class, keywords 제거 (정답 정보 노출 방지)
    }


def verify_abstract(challenge_id: str, selections: List[int], *, user_id: Optional[int] = None, api_key: Optional[str] = None) -> Dict[str, Any]:
    if get_redis():
        key = rkey("abstract", challenge_id)
        doc = redis_get_json(key)
        if not doc:
            return {"success": False, "message": "Challenge not found"}
        selections_set = set(selections or [])
        is_positive = list(doc.get("is_positive", []) or [])
        positives_set = {i for i, flag in enumerate(is_positive) if flag}
        is_pass = positives_set == selections_set
        attempts = redis_incr_attempts(key)
        if is_pass or (isinstance(attempts, int) and attempts >= 1):
            redis_del(key)
        return {
            "success": is_pass,
            "attempts": attempts if isinstance(attempts, int) and attempts >= 0 else None,
            "target_class": doc.get("target_class"),
            "keywords": doc.get("keywords", []),
            "expired": False,
        }

    # 메모리 폴백 (요약 버전)
    with ABSTRACT_SESSIONS_LOCK:
        session = ABSTRACT_SESSIONS.get(challenge_id)
    if not session:
        return {"success": False, "message": "Challenge not found"}
    if session.is_expired():
        with ABSTRACT_SESSIONS_LOCK:
            ABSTRACT_SESSIONS.pop(challenge_id, None)
        return {"success": False, "message": "Challenge expired"}
    selections_set = set(selections or [])
    positives_set = {i for i, flag in enumerate(session.is_positive) if flag}
    is_pass = positives_set == selections_set
    with ABSTRACT_SESSIONS_LOCK:
        session.attempts += 1
        if is_pass or session.attempts >= 1:
            ABSTRACT_SESSIONS.pop(challenge_id, None)
    return {
        "success": is_pass,
        "attempts": session.attempts,
        "target_class": session.target_class,
        "keywords": session.keywords,
        "expired": False,
    }


