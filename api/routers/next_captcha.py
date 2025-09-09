from fastapi import APIRouter, Header, HTTPException
from typing import Any, Dict, Optional

import json
import httpx
import uuid
from datetime import datetime, timedelta
from pathlib import Path
import threading
from bson import ObjectId
import secrets

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
from database import verify_api_key, verify_domain_access, update_api_key_usage, get_db_connection


router = APIRouter()


def generate_captcha_token(api_key: str, captcha_type: str, user_id: int) -> str:
    """
    캡차 토큰을 생성하고 데이터베이스에 저장합니다.
    """
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now() + timedelta(minutes=10)  # 10분 후 만료
    
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO captcha_tokens (token_id, api_key_id, user_id, captcha_type, expires_at, created_at)
                    VALUES (%s, %s, %s, %s, %s, NOW())
                """, (token, api_key, user_id, captcha_type, expires_at))
        return token
    except Exception as e:
        print(f"캡차 토큰 생성 오류: {e}")
        return token  # 오류가 있어도 토큰은 반환


def verify_captcha_token(token: str, api_key: str) -> tuple[bool, str]:
    """
    캡차 토큰을 검증하고 캡차 타입을 반환합니다.
    
    Returns:
        tuple: (is_valid, captcha_type)
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT id, captcha_type FROM captcha_tokens 
                    WHERE token_id = %s AND api_key_id = %s AND expires_at > NOW() AND is_used = 0
                """, (token, api_key))
                
                result = cursor.fetchone()
                if result:
                    # 토큰을 사용됨으로 표시
                    cursor.execute("""
                        UPDATE captcha_tokens SET is_used = 1, used_at = NOW() 
                        WHERE id = %s
                    """, (result[0],))
                    return True, result[1]  # (is_valid, captcha_type)
                return False, None
    except Exception as e:
        print(f"캡차 토큰 검증 오류: {e}")
        return False, None


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
def next_captcha(
    request: CaptchaRequest, 
    x_api_key: Optional[str] = Header(None),
    x_secret_key: Optional[str] = Header(None)
):
    print(f"🚀 [/api/next-captcha] 요청 시작 - API Key: {x_api_key[:20] if x_api_key else 'None'}...")
    
    # API 키 검증
    if not x_api_key:
        print("❌ API 키 없음")
        raise HTTPException(status_code=401, detail="API key required")
    
    # 데모 키 하드코딩 (홈페이지 데모용)
    DEMO_PUBLIC_KEY = 'rc_live_f49a055d62283fd02e8203ccaba70fc2'
    DEMO_SECRET_KEY = 'rc_sk_273d06a8a03799f7637083b50f4f08f2aa29ffb56fd1bfe64833850b4b16810c'
    
    # 데모 키인 경우 자동으로 비밀 키 설정 (데이터베이스 검증 우회)
    if x_api_key == DEMO_PUBLIC_KEY:
        x_secret_key = DEMO_SECRET_KEY
        api_key_info = {
            'key_id': 'demo',
            'api_key_id': 'demo',  # update_api_key_usage 함수에서 필요
            'user_id': 6,
            'is_demo': True,
            'max_requests_per_day': 1000,
            'max_requests_per_month': 30000
        }
        print(f"🎯 데모 모드: {DEMO_PUBLIC_KEY} 사용")
    else:
        # 일반 API 키 검증 (챌린지 발급 단계에서는 공개 키만 확인)
        from database import verify_api_key
        api_key_info = verify_api_key(x_api_key)
        if not api_key_info:
            raise HTTPException(status_code=401, detail="Invalid API key")
        # 비밀 키 검증은 응답 검증 단계(/api/verify-captcha)에서 수행
    
    # 도메인 검증 (Origin 헤더 확인)
    # Note: Origin 헤더는 FastAPI에서 자동으로 처리되지 않으므로 request.headers에서 직접 가져와야 함
    # 이 부분은 나중에 구현하거나 프록시에서 처리하도록 할 수 있습니다
    
    # API 키 사용량 업데이트 (데모 모드가 아닌 경우에만)
    if not api_key_info.get('is_demo', False):
        update_api_key_usage(api_key_info['api_key_id'])
    else:
        print("🎯 데모 모드: API 키 사용량 업데이트 건너뜀")
    
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
    if confidence_score >= 95:
        next_captcha_value = None  # pass
        captcha_type = "pass"
    elif confidence_score >= 80:
        next_captcha_value = "imagecaptcha"   # Basic
        captcha_type = "image"
    elif confidence_score >= 50:
        next_captcha_value = "abstractcaptcha"
        captcha_type = "abstract"
    else:
        next_captcha_value = "handwritingcaptcha"
        captcha_type = "handwriting"

    # captcha_type = "handwriting"
    # next_captcha_value = "handwritingcaptcha"

    # 안전 기본값 초기화 (예외 상황 방지)
    captcha_token: Optional[str] = None

    try:
        if not api_key_info.get('is_demo', False):
            # 일반 키: DB 저장 토큰 생성
            captcha_token = generate_captcha_token(x_api_key, captcha_type, api_key_info['user_id'])
        else:
            # 데모 키: 메모리 토큰 생성(비DB)
            captcha_token = f"demo_token_{secrets.token_urlsafe(16)}"
            print("🎯 데모 모드: 데이터베이스 토큰 저장 건너뜀")
    except Exception as e:
        print(f"⚠️ 토큰 생성 중 예외 발생: {e}")

    # 최종 안전장치: 어떤 경우에도 토큰이 비어있지 않도록
    if not captcha_token:
        captcha_token = f"fallback_token_{secrets.token_urlsafe(16)}"
        print("⚠️ 토큰 기본값(fallback) 사용")
    payload: Dict[str, Any] = {
        "message": "Behavior analysis completed",
        "status": "success",
        "confidence_score": confidence_score,
        "captcha_type": captcha_type,
        "next_captcha": next_captcha_value,
        "captcha_token": captcha_token,
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


