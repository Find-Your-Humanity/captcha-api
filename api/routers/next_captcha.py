from fastapi import APIRouter
from typing import Any, Dict

import json
import httpx
import uuid
from datetime import datetime
from pathlib import Path
import threading

from schemas.requests import CaptchaRequest
from config.settings import (
    ML_PREDICT_BOT_URL,
    DEBUG_SAVE_BEHAVIOR_DATA,
    DEBUG_BEHAVIOR_DIR,
    SAVE_BEHAVIOR_TO_MONGO,
    BEHAVIOR_MONGO_URI,
    BEHAVIOR_MONGO_DB,
    BEHAVIOR_MONGO_COLLECTION,
)
from utils.usage import track_api_usage


router = APIRouter()


_mongo_client_for_behavior = None


def _get_behavior_mongo_client():
    global _mongo_client_for_behavior
    if _mongo_client_for_behavior is not None:
        return _mongo_client_for_behavior
    if not (SAVE_BEHAVIOR_TO_MONGO and BEHAVIOR_MONGO_URI):
        return None
    try:
        from pymongo import MongoClient  # type: ignore
        _mongo_client_for_behavior = MongoClient(BEHAVIOR_MONGO_URI, serverSelectionTimeoutMS=3000)
        _ = _mongo_client_for_behavior.server_info()
        return _mongo_client_for_behavior
    except Exception:
        _mongo_client_for_behavior = None
        return None


def _save_behavior_to_mongo(doc: Dict[str, Any]) -> None:
    if not SAVE_BEHAVIOR_TO_MONGO:
        return
    client = _get_behavior_mongo_client()
    if not client or not BEHAVIOR_MONGO_DB or not BEHAVIOR_MONGO_COLLECTION:
        return
    def _worker(payload: Dict[str, Any]):
        try:
            client[BEHAVIOR_MONGO_DB][BEHAVIOR_MONGO_COLLECTION].insert_one(payload)
        except Exception:
            pass
    try:
        threading.Thread(target=_worker, args=(doc,), daemon=True).start()
    except Exception:
        try:
            client[BEHAVIOR_MONGO_DB][BEHAVIOR_MONGO_COLLECTION].insert_one(doc)
        except Exception:
            pass


@router.post("/api/next-captcha")
def next_captcha(request: CaptchaRequest):
    behavior_data = request.behavior_data
    try:
        mm = len((behavior_data or {}).get("mouseMovements", []))
        mc = len((behavior_data or {}).get("mouseClicks", []))
        se = len((behavior_data or {}).get("scrollEvents", []))
        page = (behavior_data or {}).get("pageEvents", {}) or {}
        approx_bytes = len(json.dumps({"behavior_data": behavior_data}) or "")
        print(
            f"📥 [/api/next-captcha] received: counts={{mm:{mm}, mc:{mc}, se:{se}}}, "
            f"page={{enter:{page.get('enterTime')}, exit:{page.get('exitTime')}, total:{page.get('totalTime')}}}, "
            f"approx={approx_bytes}B"
        )
        try:
            mongo_doc = {
                "behavior_data": behavior_data,
            }
            _save_behavior_to_mongo(mongo_doc)
        except Exception:
            pass
        try:
            sample = {
                "mouseMovements": (behavior_data or {}).get("mouseMovements", [])[:3],
                "mouseClicks": (behavior_data or {}).get("mouseClicks", [])[:3],
                "scrollEvents": (behavior_data or {}).get("scrollEvents", [])[:3],
                "pageEvents": page,
            }
            print(f"🔎 [/api/next-captcha] sample: {json.dumps(sample, ensure_ascii=False)[:800]}")
        except Exception:
            pass
        if DEBUG_SAVE_BEHAVIOR_DATA:
            try:
                save_dir = Path(DEBUG_BEHAVIOR_DIR)
                save_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
                fname = f"behavior_{ts}_{uuid.uuid4().hex[:8]}.json"
                fpath = save_dir / fname
                with open(fpath, "w", encoding="utf-8") as fp:
                    json.dump({"behavior_data": behavior_data}, fp, ensure_ascii=False)
                print(f"💾 [/api/next-captcha] saved behavior_data: {str(fpath.resolve())}")
            except Exception as e:
                print(f"⚠️ failed to save behavior_data: {e}")
    except Exception:
        pass

    try:
        response = httpx.post(ML_PREDICT_BOT_URL, json={"behavior_data": behavior_data})
        response.raise_for_status()
        result = response.json()
        confidence_score = result.get("confidence_score", 50)
        is_bot = result.get("is_bot", False)
        ML_SERVICE_USED = True
        print(f"🤖 ML API 결과: 신뢰도={confidence_score}, 봇여부={is_bot}")
    except Exception as e:
        print(f"❌ ML 서비스 호출 실패: {e}")
        confidence_score = 75
        is_bot = False
        ML_SERVICE_USED = False

    captcha_type = "abstract"
    next_captcha_value = "abstractcaptcha"
    payload: Dict[str, Any] = {
        "message": "Behavior analysis completed",
        "status": "success",
        "confidence_score": confidence_score,
        "captcha_type": captcha_type,
        "next_captcha": next_captcha_value,
        "behavior_data_received": len(str(behavior_data)) > 0,
        "ml_service_used": ML_SERVICE_USED,
        "is_bot_detected": is_bot if ML_SERVICE_USED else None
    }
    try:
        preview = {
            "captcha_type": captcha_type,
            "next_captcha": next_captcha_value,
            "confidence_score": confidence_score,
            "ml_service_used": ML_SERVICE_USED,
            "is_bot_detected": is_bot if ML_SERVICE_USED else None,
        }
        print(f"📦 [/api/next-captcha] response: {json.dumps(preview, ensure_ascii=False)}")
    except Exception:
        pass
    return payload


