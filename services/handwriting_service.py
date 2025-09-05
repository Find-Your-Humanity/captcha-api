from typing import Any, Dict, Optional

from infrastructure.redis_client import rkey, get_redis, redis_get_json, redis_del, redis_incr_attempts, redis_set_json
from config.settings import CAPTCHA_TTL
import uuid, time


def create_handwriting_challenge(samples: list[str], target_class: str) -> dict:
    challenge_id = uuid.uuid4().hex
    ttl_seconds = CAPTCHA_TTL
    if get_redis():
        doc = {
            "type": "handwriting",
            "cid": challenge_id,
            "samples": samples,
            "target_class": target_class,
            "attempts": 0,
            "created_at": time.time(),
        }
        redis_set_json(rkey("handwriting", challenge_id), doc, ttl_seconds)
    return {
        "challenge_id": challenge_id,
        "samples": samples,
        "ttl": ttl_seconds,
        "message": "Handwriting challenge created successfully",
    }


def verify_handwriting(challenge_id: str, text_norm: str, *, user_id: Optional[int] = None, api_key: Optional[str] = None) -> Dict[str, Any]:
    """검증 핵심만 서비스로 분리. OCR 호출/전처리는 라우터 단계에 존치 예정."""
    redis_doc = None
    redis_key = None
    if get_redis() and challenge_id:
        redis_key = rkey("handwriting", challenge_id)
        redis_doc = redis_get_json(redis_key)
    if not redis_doc:
        return {"success": False, "message": "Challenge not found"}
    target_answer_class = str((redis_doc or {}).get("target_class") or "")
    is_match = (text_norm == (target_answer_class or "")) and len(target_answer_class or "") > 0
    if redis_doc and redis_key:
        attempts = redis_incr_attempts(redis_key)
        if is_match or (isinstance(attempts, int) and attempts >= 1):
            redis_del(redis_key)
    return {"success": is_match}


