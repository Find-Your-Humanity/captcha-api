from fastapi import APIRouter, Header, HTTPException
from typing import Any, Dict, Optional

import json
import httpx
import uuid
from datetime import datetime
from pathlib import Path
import threading
from bson import ObjectId

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
from database import verify_api_key, verify_domain_access, update_api_key_usage


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
def next_captcha(request: CaptchaRequest, x_api_key: Optional[str] = Header(None)):
    # API 키 검증
    if not x_api_key:
        raise HTTPException(status_code=401, detail="API key required")
    
    api_key_info = verify_api_key(x_api_key)
    if not api_key_info:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    # 도메인 검증 (Origin 헤더 확인)
    # Note: Origin 헤더는 FastAPI에서 자동으로 처리되지 않으므로 request.headers에서 직접 가져와야 함
    # 이 부분은 나중에 구현하거나 프록시에서 처리하도록 할 수 있습니다
    
    # API 키 사용량 업데이트
    update_api_key_usage(api_key_info['api_key_id'])
    
    behavior_data = request.behavior_data
    correlation_id = ObjectId()
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
                "_id": correlation_id,
                "behavior_data": behavior_data,
                "createdAt": datetime.utcnow().isoformat(),
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

    # 점수 저장: behavior_data의 생성된 correlation_id를 참조하여 별도 컬렉션에 저장
    try:
        client = _get_behavior_mongo_client()
        if client and BEHAVIOR_MONGO_DB:
            # score는 basic_data_score 컬렉션에 저장
            score_coll = client[BEHAVIOR_MONGO_DB]["behavior_data_score"]
            score_coll.insert_one({
                "behavior_data_id": correlation_id,
                "confidence_score": confidence_score,
            })
    except Exception:
        pass

    # [계획된 로직 안내 - 아직 미적용]
    # 사용자 행동 데이터 신뢰도 점수(confidence_score)를 기준으로 다음 캡차 타입을 결정합니다.
    # - 95 이상: 추가 캡차 없이 통과(pass)
    # - 80 이상: 이미지 그리드 캡차(Basic) → "imagecaptcha"
    # - 50 이상: 추상 이미지 캡차 → "abstractcaptcha"
    # - 50 미만: 손글씨 캡차 → "handwritingcaptcha"
    #
    # 아래는 실제 적용 시 참고할 예시 코드입니다. (주석 처리)
    # if confidence_score >= 95:
    #     next_captcha_value = None  # pass
    #     captcha_type = "pass"
    # elif confidence_score >= 80:
    #     next_captcha_value = "imagecaptcha"   # Basic
    #     captcha_type = "image"
    # elif confidence_score >= 50:
    #     next_captcha_value = "abstractcaptcha"
    #     captcha_type = "abstract"
    # else:
    #     next_captcha_value = "handwritingcaptcha"
    #     captcha_type = "handwriting"

    captcha_type = "image"
    next_captcha_value = "imagecaptcha"
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


