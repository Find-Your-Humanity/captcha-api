from fastapi import APIRouter, Header, HTTPException, Request
from typing import Any, Dict, Optional

import json
import httpx
import uuid
from datetime import datetime, timedelta
from pathlib import Path
import threading
from bson import ObjectId
import secrets
import re

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
from utils.rate_limiter import rate_limiter
from utils.ip_rate_limiter import ip_rate_limiter
from database import verify_domain_access, update_api_key_usage, get_db_connection, log_request, log_request_to_request_logs, update_daily_api_stats, update_daily_api_stats_by_key
from database import verify_api_key_with_secret, verify_api_key_auto_secret
from infrastructure.redis_client import (
    create_checkbox_session, 
    get_checkbox_session, 
    increment_checkbox_attempts, 
    is_checkbox_session_blocked
)


router = APIRouter()


def generate_captcha_token(api_key_id: int, captcha_type: str, user_id: int) -> str:
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
                """, (token, api_key_id, user_id, captcha_type, expires_at))
        return token
    except Exception as e:
        print(f"캡차 토큰 생성 오류: {e}")
        return token  # 오류가 있어도 토큰은 반환


def verify_captcha_token(token: str, api_key_id: int) -> tuple[bool, str]:
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
                """, (token, api_key_id))
                
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

def _is_mobile_user_agent(user_agent: str) -> bool:
    """
    User-Agent 문자열을 분석하여 모바일/태블릿 환경인지 판단합니다.
    """
    if not user_agent:
        print("⚠️ User-Agent가 비어있음")
        return False
    
    # 모바일/태블릿 관련 키워드 패턴
    mobile_patterns = [
        r'mobile', r'android', r'iphone', r'ipad', r'ipod',
        r'blackberry', r'windows phone', r'opera mini',
        r'kindle', r'silk', r'webos', r'palm'
    ]
    
    user_agent_lower = user_agent.lower()
    matched_patterns = []
    
    for pattern in mobile_patterns:
        if re.search(pattern, user_agent_lower):
            matched_patterns.append(pattern)
    
    if matched_patterns:
        print(f"🎯 모바일 패턴 매칭: {matched_patterns}")
        return True
    
    print("💻 데스크톱 환경으로 판단")
    return False


def _save_behavior_to_mongo(doc: Dict[str, Any], user_agent: Optional[str] = None, is_bot: bool = False) -> None:
    """
    behavior_data를 MongoDB에 저장합니다.
    모바일 환경에서는 저장하지 않습니다.
    봇 여부에 따라 다른 컬렉션을 사용합니다.
    """
    if not SAVE_BEHAVIOR_TO_MONGO:
        return
    
    # 모바일 환경 감지 및 저장 건너뛰기
    if _is_mobile_user_agent(user_agent or ""):
        print("🛡️ 모바일 환경 감지: behavior_data MongoDB 저장 건너뜀")
        return
    
    client = _get_behavior_mongo_client()
    if not client or not BEHAVIOR_MONGO_DB or not BEHAVIOR_MONGO_COLLECTION:
        return
    
    # 봇 여부에 따라 컬렉션 이름 결정
    collection_name = f"{BEHAVIOR_MONGO_COLLECTION}_bot" if is_bot else BEHAVIOR_MONGO_COLLECTION
    print(f"🤖 봇 여부: {is_bot}, 사용할 컬렉션: {collection_name}")
    
    def _worker(payload: Dict[str, Any]):
        try:
            client[BEHAVIOR_MONGO_DB][collection_name].insert_one(payload)
        except Exception:
            pass
    try:
        threading.Thread(target=_worker, args=(doc,), daemon=True).start()
    except Exception:
        try:
            client[BEHAVIOR_MONGO_DB][collection_name].insert_one(doc)
        except Exception:
            pass


@router.post("/api/next-captcha")
def next_captcha(
    request: CaptchaRequest, 
    x_api_key: Optional[str] = Header(None),
    x_secret_key: Optional[str] = Header(None),
    user_agent: Optional[str] = Header(None),
    http_request: Request = None,
    is_bot: Optional[str] = Header(None)
):
    print(f"🚀 [/api/next-captcha] 요청 시작 - API Key: {x_api_key[:20] if x_api_key else 'None'}...")
    
    # 클라이언트 IP 추출
    client_ip = ip_rate_limiter.get_client_ip(http_request)
    print(f"🌐 클라이언트 IP: {client_ip}")
    
    # IP 기반 Rate Limiting 체크
    try:
        ip_rate_limit_result = ip_rate_limiter.check_ip_rate_limit(
            ip_address=client_ip,
            rate_limit_per_minute=30,  # IP당 분당 30회
            rate_limit_per_hour=500,   # IP당 시간당 500회
            rate_limit_per_day=2000,   # IP당 일당 2000회
            api_key=x_api_key          # API 키 전달 (MySQL 저장용)
        )
        print(f"✅ IP Rate Limiting 통과: {ip_rate_limit_result['minute_remaining']}/min, {ip_rate_limit_result['hour_remaining']}/hour, {ip_rate_limit_result['day_remaining']}/day 남음")
    except HTTPException as e:
        print(f"❌ IP Rate Limiting 초과: {e.detail}")
        raise e
    except Exception as e:
        print(f"⚠️ IP Rate Limiting 오류 (요청 허용): {e}")
        # Redis 오류 등으로 IP Rate Limiting이 실패해도 요청은 허용 (fail-open)
    
    # User-Agent 디버깅 로그
    print(f"🔍 User-Agent: {user_agent}")
    is_mobile = _is_mobile_user_agent(user_agent or "")
    print(f"📱 모바일 환경 감지: {is_mobile}")
    
    # 봇 여부 확인
    is_bot_request = is_bot and is_bot.lower() == 'true'
    print(f"🤖 봇 요청 여부: {is_bot_request}")
    
    # API 키/시크릿 검증 (데모 모드 예외 허용: 공개키만으로 조회)
    if not x_api_key:
        print("❌ API 키 없음")
        raise HTTPException(status_code=401, detail="API key required")
    
    # 데모 키 하드코딩 (홈페이지 데모용)
    DEMO_PUBLIC_KEY = 'rc_live_f49a055d62283fd02e8203ccaba70fc2'
    DEMO_SECRET_KEY = 'rc_sk_273d06a8a03799f7637083b50f4f08f2aa29ffb56fd1bfe64833850b4b16810c'
    
    # 데모 키 처리 (환경 변수 DEMO_SECRET_KEY 필요)
    if x_api_key == DEMO_PUBLIC_KEY:
        # 데모: 공개키만으로 DB에서 is_demo 키 확인 후 통과 (시크릿 불요)
        api_key_info = verify_api_key_auto_secret(x_api_key)
        if not api_key_info or not api_key_info.get('is_demo'):
            raise HTTPException(status_code=401, detail="Invalid demo api key")
        print(f"🎯 데모 모드(DB): {DEMO_PUBLIC_KEY} 사용")
    else:
        # 일반: 챌린지 요청은 공개키만, 최종 검증은 공개키+비밀키
        if not x_secret_key:
            # 2단계: 공개키만으로 챌린지 요청 (브라우저에서 직접 호출)
            api_key_info = verify_api_key_auto_secret(x_api_key)
            if not api_key_info:
                raise HTTPException(status_code=401, detail="Invalid API key")
            print(f"🌐 챌린지 요청 모드: {x_api_key[:20]}... (공개키만)")
        else:
            # 4단계: 공개키+비밀키로 최종 검증 (사용자 서버에서 호출)
            api_key_info = verify_api_key_with_secret(x_api_key, x_secret_key)
            if not api_key_info:
                raise HTTPException(status_code=401, detail="Invalid API key or secret key")
            print(f"🔐 최종 검증 모드: {x_api_key[:20]}... (공개키+비밀키)")
    
    # Rate Limiting 체크
    try:
        rate_limit_per_minute = api_key_info.get('rate_limit_per_minute', 60)
        rate_limit_per_day = api_key_info.get('rate_limit_per_day', 1000)
        
        print(f"🔒 Rate Limiting 체크: {rate_limit_per_minute}/min, {rate_limit_per_day}/day")
        
        # Rate Limiting 검증
        rate_limit_result = rate_limiter.check_rate_limit(
            api_key=x_api_key,
            rate_limit_per_minute=rate_limit_per_minute,
            rate_limit_per_day=rate_limit_per_day
        )
        
        print(f"✅ Rate Limiting 통과: {rate_limit_result['minute_remaining']}/min, {rate_limit_result['day_remaining']}/day 남음")
        
    except HTTPException as e:
        print(f"❌ Rate Limiting 초과: {e.detail}")
        raise e
    except Exception as e:
        print(f"⚠️ Rate Limiting 오류 (요청 허용): {e}")
        # Redis 오류 등으로 Rate Limiting이 실패해도 요청은 허용 (fail-open)
    
    # 도메인 검증 (Origin 헤더 확인)
    # Note: Origin 헤더는 FastAPI에서 자동으로 처리되지 않으므로 request.headers에서 직접 가져와야 함
    # 이 부분은 나중에 구현하거나 프록시에서 처리하도록 할 수 있습니다
    
    # 사용량 집계는 검증 단계(/api/verify-captcha)에서 타입별로 처리합니다.
    if api_key_info.get('is_demo', False):
        print("🎯 데모 모드: 발급 단계에서 사용량 업데이트 없음")
        
        # 데모 키도 실제 캡차 발급 진행
    
    # 체크박스 세션 생성 또는 조회
    checkbox_session_id = request.session_id or str(uuid.uuid4())
    print(f"🔑 체크박스 세션 ID: {checkbox_session_id}")
    
    # 기존 세션이 있는지 확인
    existing_session = get_checkbox_session(checkbox_session_id)
    if not existing_session:
        # 새 세션 생성
        create_checkbox_session(checkbox_session_id, ttl=300)  # 5분 TTL
        print(f"✅ 새 체크박스 세션 생성: {checkbox_session_id}")
    else:
        print(f"📋 기존 체크박스 세션 사용: {checkbox_session_id}")
    
    # 세션이 차단되었는지 확인
    if is_checkbox_session_blocked(checkbox_session_id):
        print(f"🚫 차단된 세션: {checkbox_session_id}")
        return {
            "message": "Session blocked due to suspicious activity",
            "status": "blocked",
            "session_id": checkbox_session_id,
            "is_blocked": True,
            "captcha_type": "",
            "next_captcha": "",
            "captcha_token": None
        }
    
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
            _save_behavior_to_mongo(mongo_doc, user_agent, is_bot_request)
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
        if DEBUG_SAVE_BEHAVIOR_DATA and not _is_mobile_user_agent(user_agent or ""):
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
        elif DEBUG_SAVE_BEHAVIOR_DATA and _is_mobile_user_agent(user_agent or ""):
            print("🛡️ 모바일 환경 감지: behavior_data 파일 저장 건너뜀")
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
    # 모바일 환경에서는 저장하지 않음
    if not _is_mobile_user_agent(user_agent or ""):
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
    else:
        print("🛡️ 모바일 환경 감지: behavior_data_score MongoDB 저장 건너뜀")

    # 체크박스 시도 횟수 추적 및 봇 차단 로직
    is_low_score = confidence_score <= 9
    session_data = increment_checkbox_attempts(checkbox_session_id, is_low_score=is_low_score, ttl=300)
    
    if session_data and session_data.get("is_blocked", False):
        print(f"🚫 봇 차단: 세션 {checkbox_session_id}, 낮은 점수 시도 횟수: {session_data.get('low_score_attempts', 0)}")
        return {
            "message": "Session blocked due to repeated low confidence scores",
            "status": "blocked",
            "session_id": checkbox_session_id,
            "is_blocked": True,
            "confidence_score": confidence_score,
            "low_score_attempts": session_data.get("low_score_attempts", 0),
            "captcha_type": "",
            "next_captcha": "",
            "captcha_token": None
        }
    
    # 모바일 환경에서는 체크박스만 표시하고 다음 캡차 단계로 진행하지 않음
    if _is_mobile_user_agent(user_agent or ""):
        print("📱 모바일 환경: 체크박스만 표시, 다음 캡차 단계 없음")
        next_captcha_value = None  # 다음 캡차 없음
        captcha_type = "pass"      # 통과 처리
    else:
          # 데스크톱 환경: 신뢰도 점수에 따른 캡차 타입 결정
        if confidence_score >= 90:
            next_captcha_value = None  # pass
            captcha_type = "pass"
        elif confidence_score >= 60:
            next_captcha_value = "imagecaptcha"   # Basic
            captcha_type = "image"
        elif confidence_score >= 40:
            next_captcha_value = "abstractcaptcha"
            captcha_type = "abstract"
        elif confidence_score >= 10:
            next_captcha_value = "handwritingcaptcha"
            captcha_type = "handwriting"
        else:   
            # confidence_score 9 이하: 봇일 확률 높음, 시도 횟수 제한 적용
            print(f"⚠️ 낮은 신뢰도 점수: {confidence_score}, 시도 횟수: {session_data.get('low_score_attempts', 0) if session_data else 0}")
            next_captcha_value = ""  # 캡차 비활성화
            captcha_type = ""
        # 데스크톱 환경: 모든 경우에 handwritingcaptcha로 설정
        # print(f"🎯 모든 경우에 handwritingcaptcha로 설정 (신뢰도: {confidence_score})")
        # next_captcha_value = "handwritingcaptcha"
        # captcha_type = "handwriting"

    # 안전 기본값 초기화 (예외 상황 방지)
    captcha_token: Optional[str] = None

    try:
        if not api_key_info.get('is_demo', False):
            # 일반 키: DB 저장 토큰 생성 (정수형 api_key_id 사용)
            captcha_token = generate_captcha_token(api_key_info['api_key_id'], captcha_type, api_key_info['user_id'])
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
        "is_bot_detected": is_bot if ML_SERVICE_USED else None,
        "session_id": checkbox_session_id,
        "is_blocked": False,
        "attempts": session_data.get("attempts", 0) if session_data else 0,
        "low_score_attempts": session_data.get("low_score_attempts", 0) if session_data else 0
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
    
    # API 요청 로그 저장
    try:
        if api_key_info and not api_key_info.get('is_demo', False):
            # 상세 로그 저장 (api_request_logs 테이블) - 실제 captcha_type 사용
            log_request(
                user_id=api_key_info['user_id'],
                api_key=x_api_key,
                path="/api/next-captcha",
                api_type=captcha_type,  # 실제 결정된 captcha_type 사용
                method="POST",
                status_code=200,
                response_time=0  # next-captcha는 응답시간 측정하지 않음
            )
            
            # request_logs 테이블에도 로그 저장 - 실제 captcha_type 사용
            log_request_to_request_logs(
                user_id=api_key_info['user_id'],
                api_key=x_api_key,
                path="/api/next-captcha",
                api_type=captcha_type,  # 실제 결정된 captcha_type 사용
                method="POST",
                status_code=200,
                response_time=0,
                user_agent=None
            )
            
            # 일별 통계 업데이트 (전역) - 실제 captcha_type 사용
            update_daily_api_stats(captcha_type, True, 0)
            
            # 사용자별 일별 통계 업데이트 - 실제 captcha_type 사용
            update_daily_api_stats_by_key(
                user_id=api_key_info['user_id'],
                api_key=x_api_key,
                api_type=captcha_type,  # 실제 결정된 captcha_type 사용
                response_time=0,
                is_success=True
            )
            
            print(f"📝 [/api/next-captcha] 로그 및 통계 저장 완료")
    except Exception as e:
        print(f"⚠️ [/api/next-captcha] 로그 저장 실패: {e}")
    
    return payload


