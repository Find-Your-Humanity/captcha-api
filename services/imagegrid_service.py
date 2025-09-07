from typing import Any, Dict, List, Optional
import os, time, uuid
from domain.models import ImageGridCaptchaSession
from infrastructure.redis_client import get_redis, rkey, redis_set_json, redis_get_json, redis_del, redis_incr_attempts
from state.sessions import IMAGE_GRID_SESSIONS, IMAGE_GRID_LOCK
from config.settings import CAPTCHA_TTL


def create_imagegrid_challenge() -> Dict[str, Any]:
    key: Optional[str] = None
    url: Optional[str] = None
    target_label: Optional[str] = None
    correct_cells: List[int] = []
    try:
        from pymongo import MongoClient  # type: ignore
        uri = os.getenv("MONGO_URI", os.getenv("MONGO_URL", ""))
        dbn = os.getenv("MONGO_DB", "")
        # image captcha는 기본적으로 basic_label_filtered 컬렉션을 사용하도록 고정
        # 환경변수 MONGO_BASIC_COLLECTION으로 오버라이드 가능
        coln = os.getenv("MONGO_BASIC_COLLECTION", "basic_label_filtered")
        client = MongoClient(uri, serverSelectionTimeoutMS=3000)
        coll = client[dbn][coln]
        doc = coll.aggregate([{"$sample": {"size": 1}}]).next()
    except Exception:
        raise

    key = str(doc.get("key", ""))
    url = str(doc.get("url", ""))
    target_label = str(doc.get("target_label", ""))
    correct_cells = list(doc.get("correct_cells", []) or [])

    challenge_id = uuid.uuid4().hex
    session = ImageGridCaptchaSession(
        challenge_id=challenge_id,
        image_url=url,
        ttl_seconds=CAPTCHA_TTL,
        created_at=time.time(),
        target_label=target_label or "",
        correct_cells=correct_cells,
    )

    if get_redis():
        try:
            doc = {
                "type": "imagegrid",
                "cid": challenge_id,
                "image_url": url,
                "attempts": 0,
                "created_at": session.created_at,
                "target_label": session.target_label,
                "correct_cells": list(session.correct_cells or []),
            }
            ok = redis_set_json(rkey("imagegrid", challenge_id), doc, session.ttl_seconds)
            if not ok:
                raise RuntimeError("redis setex failed")
        except Exception:
            with IMAGE_GRID_LOCK:
                IMAGE_GRID_SESSIONS[challenge_id] = session
    else:
        with IMAGE_GRID_LOCK:
            IMAGE_GRID_SESSIONS[challenge_id] = session

    # 질문 문구 매핑 적용
    label_message_map = {
        "person": "사람이 포함된 이미지를 고르시오",
        "car": "차가 포함된 이미지를 고르시오",
        "dog": "개가 포함된 이미지를 고르시오",
        "cat": "고양이가 포함된 이미지를 고르시오",
        "bus": "버스가 포함된 이미지를 고르시오",
        "bicycle": "자전거가 포함된 이미지를 고르시오",
    }
    question_text = label_message_map.get((target_label or "").lower(), f"{target_label} 이미지를 모두 고르시오")

    return {
        "challenge_id": challenge_id,
        "url": url,
        "ttl": session.ttl_seconds,
        "grid_size": 3,
        "target_label": target_label,
        "question": question_text,
    }


def verify_imagegrid(challenge_id: str, selections: List[int]) -> Dict[str, Any]:
    if get_redis():
        key = rkey("imagegrid", challenge_id)
        doc = redis_get_json(key)
        if not doc:
            return {"success": False, "message": "Challenge not found"}
        sel = sorted(set(int(x) for x in (selections or [])))
        target_label = str(doc.get("target_label", ""))
        correct = sorted(set(int(x) for x in (doc.get("correct_cells", []) or [])))
        ok = sel == correct
        attempts = redis_incr_attempts(key)
        if ok or (isinstance(attempts, int) and attempts >= 1):
            redis_del(key)
        payload = {
            "success": ok,
            "attempts": attempts if isinstance(attempts, int) and attempts >= 0 else None,
            "target_label": target_label,
            "correct_cells": correct,
            "user_selections": sel,
            "boxes": [],
        }
        if not ok and isinstance(attempts, int) and attempts >= 1:
            payload["downshift"] = True
        return payload

    with IMAGE_GRID_LOCK:
        session = IMAGE_GRID_SESSIONS.get(challenge_id)
    if not session:
        return {"success": False, "message": "Challenge not found"}
    if (time.time() - session.created_at) > session.ttl_seconds:
        with IMAGE_GRID_LOCK:
            IMAGE_GRID_SESSIONS.pop(challenge_id, None)
        return {"success": False, "message": "Challenge expired"}

    sel = sorted(set(int(x) for x in (selections or [])))
    target_label = session.target_label
    correct = sorted(set(session.correct_cells or []))
    ok = sel == correct
    with IMAGE_GRID_LOCK:
        session.attempts += 1
        attempts = session.attempts
        if ok or attempts >= 1:
            IMAGE_GRID_SESSIONS.pop(challenge_id, None)
    payload = {
        "success": ok,
        "attempts": attempts,
        "target_label": target_label,
        "correct_cells": correct,
        "user_selections": sel,
        "boxes": [],
    }
    if not ok and attempts >= 1:
        payload["downshift"] = True
    return payload


