from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
from typing import Optional, Dict, Any
import json
from datetime import datetime

from database import verify_api_key, verify_domain_access, update_api_key_usage, log_request, get_db_connection, verify_captcha_token

router = APIRouter()


class VerifyCaptchaRequest(BaseModel):
    captcha_token: str
    captcha_response: str


class VerifyCaptchaResponse(BaseModel):
    success: bool
    message: str
    timestamp: str


def verify_api_key_auto_secret(api_key: str) -> Optional[Dict[str, Any]]:
    """
    API 키만으로 검증합니다. 비밀 키는 서버에서 자동으로 처리합니다.
    데모 키의 경우 환경 변수에서, 일반 키의 경우 데이터베이스에서 비밀 키를 가져옵니다.
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # API 키 조회
                cursor.execute("""
                    SELECT 
                        ak.id, ak.user_id, ak.name, ak.is_active, ak.rate_limit_per_minute,
                        ak.rate_limit_per_day, ak.usage_count, ak.last_used_at, ak.allowed_origins,
                        ak.is_demo, ak.secret_key,
                        u.email, us.plan_id, p.name AS plan_name
                    FROM api_keys ak
                    LEFT JOIN users u ON ak.user_id = u.id
                    LEFT JOIN user_subscriptions us ON u.id = us.user_id
                    LEFT JOIN plans p ON us.plan_id = p.id
                    WHERE ak.key_id = %s AND ak.is_active = 1
                """, (api_key,))
                
                result = cursor.fetchone()
                if not result:
                    return None
                
                # 데모 키인 경우 환경 변수에서 비밀 키 확인
                if result[9] == 1:  # is_demo = 1
                    import os
                    demo_secret_key = os.getenv('DEMO_SECRET_KEY')
                    if not demo_secret_key:
                        print("경고: DEMO_SECRET_KEY 환경 변수가 설정되지 않았습니다.")
                        return None
                    # 데모 키는 항상 유효 (환경 변수에 있으면)
                    print(f"데모 키 검증 성공: {api_key}")
                else:
                    # 일반 키인 경우 데이터베이스의 비밀 키가 있는지 확인
                    if not result[10]:  # secret_key가 없으면
                        print(f"일반 키에 비밀 키가 없습니다: {api_key}")
                        return None
                    print(f"일반 키 검증 성공: {api_key}")
                
                return {
                    'api_key_id': result[0],
                    'user_id': result[1],
                    'key_name': result[2],
                    'is_active': result[3],
                    'rate_limit_per_minute': result[4],
                    'rate_limit_per_day': result[5],
                    'usage_count': result[6],
                    'last_used_at': result[7],
                    'allowed_origins': result[8],
                    'is_demo': result[9],
                    'user_email': result[11],
                    'plan_id': result[12],
                    'plan_name': result[13],
                }
    except Exception as e:
        print(f"API 키 자동 검증 오류: {e}")
        return None


def verify_api_key_with_secret(api_key: str, secret_key: str) -> Optional[Dict[str, Any]]:
    """
    API 키와 비밀 키 쌍을 검증합니다.
    데모 키의 경우 환경 변수에서 비밀 키를 확인합니다.
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # 먼저 일반 API 키 조회
                cursor.execute("""
                    SELECT 
                        ak.id, ak.user_id, ak.name, ak.is_active, ak.rate_limit_per_minute,
                        ak.rate_limit_per_day, ak.usage_count, ak.last_used_at, ak.allowed_origins,
                        ak.is_demo, ak.secret_key,
                        u.email, us.plan_id, p.name AS plan_name
                    FROM api_keys ak
                    LEFT JOIN users u ON ak.user_id = u.id
                    LEFT JOIN user_subscriptions us ON u.id = us.user_id
                    LEFT JOIN plans p ON us.plan_id = p.id
                    WHERE ak.key_id = %s AND ak.is_active = 1
                """, (api_key,))
                
                result = cursor.fetchone()
                if not result:
                    return None
                
                # 데모 키인 경우 환경 변수에서 비밀 키 확인
                if result[9] == 1:  # is_demo = 1
                    import os
                    demo_secret_key = os.getenv('DEMO_SECRET_KEY')
                    if secret_key != demo_secret_key:
                        return None
                else:
                    # 일반 키인 경우 데이터베이스의 비밀 키와 비교
                    if secret_key != result[10]:  # secret_key
                        return None
                
                return {
                    'api_key_id': result[0],
                    'user_id': result[1],
                    'key_name': result[2],
                    'is_active': result[3],
                    'rate_limit_per_minute': result[4],
                    'rate_limit_per_day': result[5],
                    'usage_count': result[6],
                    'last_used_at': result[7],
                    'allowed_origins': result[8],
                    'is_demo': result[9],
                    'user_email': result[11],
                    'plan_id': result[12],
                    'plan_name': result[13],
                }
    except Exception as e:
        print(f"API 키/비밀 키 검증 오류: {e}")
        return None


@router.post("/api/verify-captcha", response_model=VerifyCaptchaResponse)
def verify_captcha(
    request: VerifyCaptchaRequest,
    x_api_key: Optional[str] = Header(None),
    x_secret_key: Optional[str] = Header(None)
):
    """
    캡차 응답을 검증합니다. (공개 키와 비밀 키 모두 사용)
    """
    start_time = datetime.now()
    
    # API 키와 비밀 키 검증
    if not x_api_key or not x_secret_key:
        raise HTTPException(status_code=401, detail="API key and secret key required")
    
    # 공개 키와 비밀 키 쌍 검증
    api_key_info = verify_api_key_with_secret(x_api_key, x_secret_key)
    if not api_key_info:
        raise HTTPException(status_code=401, detail="Invalid API key or secret key")
    
    # 도메인 검증 (Origin 헤더 확인)
    # TODO: Origin 헤더 검증 로직 추가
    
    # 캡차 토큰 검증 로직
    if not request.captcha_token or not request.captcha_response:
        raise HTTPException(status_code=400, detail="Invalid captcha token or response")
    
    # 토큰 검증 및 캡차 타입 가져오기
    token_valid, captcha_type = verify_captcha_token(request.captcha_token, api_key_info['api_key_id'])
    if not token_valid:
        raise HTTPException(status_code=400, detail="Invalid or expired captcha token")
    
    # API 키 사용량 업데이트 (캡차 타입별)
    update_api_key_usage(api_key_info['api_key_id'], captcha_type)
    
    # 성공 응답
    response_time = int((datetime.now() - start_time).total_seconds() * 1000)
    
    # 로그 저장
    log_request(
        user_id=api_key_info['user_id'],
        api_key=x_api_key,
        path="/api/verify-captcha",
        api_type="verify_captcha",
        method="POST",
        status_code=200,
        response_time=response_time
    )
    
    return VerifyCaptchaResponse(
        success=True,
        message="Captcha verification successful",
        timestamp=datetime.now().isoformat()
    )
