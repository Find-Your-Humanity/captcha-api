"""
안전한 캡차 API 엔드포인트
ALTCHA 스타일의 서버 사이드 검증 구조
"""
from fastapi import APIRouter, HTTPException, Query, Request
from typing import Optional, Dict, Any
import secrets
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta
from database import get_db_connection, verify_api_key, verify_domain_access, update_api_key_usage, log_request

router = APIRouter(prefix="/api", tags=["Secure Captcha"])

# 서버 사이드 HMAC 키 (환경변수에서 가져와야 함)
HMAC_SECRET = "realcaptcha-hmac-secret-key-2024"  # 실제로는 환경변수에서 가져와야 함

def create_challenge_token(api_key_id: int, domain: str, captcha_type: str) -> str:
    """챌린지 토큰 생성 (서명 포함)"""
    payload = {
        "api_key_id": api_key_id,
        "domain": domain,
        "captcha_type": captcha_type,
        "timestamp": int(time.time()),
        "nonce": secrets.token_hex(16)
    }
    
    # HMAC 서명 생성
    payload_str = json.dumps(payload, sort_keys=True)
    signature = hmac.new(
        HMAC_SECRET.encode(),
        payload_str.encode(),
        hashlib.sha256
    ).hexdigest()
    
    # 토큰에 서명 포함
    token_data = {
        "payload": payload,
        "signature": signature
    }
    
    return json.dumps(token_data)

def verify_challenge_token(token: str) -> Optional[Dict]:
    """챌린지 토큰 검증"""
    try:
        token_data = json.loads(token)
        payload = token_data["payload"]
        signature = token_data["signature"]
        
        # 서명 검증
        payload_str = json.dumps(payload, sort_keys=True)
        expected_signature = hmac.new(
            HMAC_SECRET.encode(),
            payload_str.encode(),
            hashlib.sha256
        ).hexdigest()
        
        if not hmac.compare_digest(signature, expected_signature):
            return None
            
        # 토큰 만료 검증 (5분)
        if int(time.time()) - payload["timestamp"] > 300:
            return None
            
        return payload
        
    except Exception:
        return None

@router.get("/challenge")
async def get_challenge(
    domain: str = Query(..., description="요청 도메인"),
    captcha_type: str = Query("image", description="캡차 타입"),
    request: Request = None
):
    """
    안전한 캡차 챌린지 생성
    - 도메인 기반으로 API 키 검증
    - 서명된 토큰으로 챌린지 생성
    """
    try:
        # Origin 헤더에서 도메인 추출
        origin = request.headers.get("origin", "")
        if not origin:
            raise HTTPException(status_code=400, detail="Origin header required")
        
        # 도메인에서 호스트 추출
        if origin.startswith("http://") or origin.startswith("https://"):
            request_domain = origin.split("://")[1].split("/")[0]
        else:
            request_domain = origin
            
        # 도메인 기반으로 API 키 검증
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # 도메인에 해당하는 활성 API 키 찾기
                cursor.execute("""
                    SELECT ak.id, ak.key_id, ak.user_id, ak.allowed_origins, ak.usage_count
                    FROM api_keys ak
                    WHERE ak.is_active = 1
                    AND (
                        ak.allowed_origins IS NULL 
                        OR ak.allowed_origins = '[]'
                        OR JSON_CONTAINS(ak.allowed_origins, %s)
                        OR JSON_CONTAINS(ak.allowed_origins, %s)
                    )
                    ORDER BY ak.created_at DESC
                    LIMIT 1
                """, (f'"{request_domain}"', f'"*"'))
                
                api_key_data = cursor.fetchone()
                
                if not api_key_data:
                    raise HTTPException(status_code=403, detail="No valid API key found for this domain")
                
                # 사용량 업데이트
                cursor.execute("""
                    UPDATE api_keys 
                    SET usage_count = usage_count + 1, last_used_at = NOW()
                    WHERE id = %s
                """, (api_key_data["id"],))
                
                conn.commit()
        
        # 챌린지 토큰 생성
        challenge_token = create_challenge_token(
            api_key_data["id"], 
            request_domain, 
            captcha_type
        )
        
        # 챌린지 데이터 생성 (실제 캡차 데이터는 별도 로직)
        challenge_data = {
            "challenge_token": challenge_token,
            "captcha_type": captcha_type,
            "domain": request_domain,
            "expires_at": int(time.time()) + 300,  # 5분 후 만료
            "challenge_id": secrets.token_hex(16)
        }
        
        # 로그 기록
        log_request(
            api_key_data["id"],
            api_key_data["user_id"],
            "GET",
            "/api/challenge",
            request_domain,
            {"captcha_type": captcha_type},
            "success"
        )
        
        return {
            "success": True,
            "challenge": challenge_data,
            "message": "Challenge created successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create challenge: {str(e)}")

@router.post("/verify")
async def verify_solution(
    challenge_token: str,
    solution: Dict[str, Any],
    request: Request = None
):
    """
    캡차 솔루션 검증
    - 서명된 토큰 검증
    - 솔루션 정확성 검증
    """
    try:
        # 토큰 검증
        token_payload = verify_challenge_token(challenge_token)
        if not token_payload:
            raise HTTPException(status_code=400, detail="Invalid or expired challenge token")
        
        # API 키 정보 조회
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT ak.id, ak.key_id, ak.user_id, ak.allowed_origins
                    FROM api_keys ak
                    WHERE ak.id = %s AND ak.is_active = 1
                """, (token_payload["api_key_id"],))
                
                api_key_data = cursor.fetchone()
                if not api_key_data:
                    raise HTTPException(status_code=403, detail="Invalid API key")
        
        # Origin 검증
        origin = request.headers.get("origin", "")
        if origin:
            if origin.startswith("http://") or origin.startswith("https://"):
                request_domain = origin.split("://")[1].split("/")[0]
            else:
                request_domain = origin
                
            if request_domain != token_payload["domain"]:
                raise HTTPException(status_code=403, detail="Domain mismatch")
        
        # 솔루션 검증 (실제 캡차 검증 로직)
        is_valid = True  # 실제로는 캡차 타입에 따른 검증 로직 필요
        confidence_score = 85  # 실제로는 ML 모델 결과
        
        # 로그 기록
        log_request(
            api_key_data["id"],
            api_key_data["user_id"],
            "POST",
            "/api/verify",
            token_payload["domain"],
            {"solution": solution, "is_valid": is_valid},
            "success" if is_valid else "failed"
        )
        
        return {
            "success": is_valid,
            "confidence_score": confidence_score,
            "is_bot_detected": confidence_score < 50,
            "verification_token": secrets.token_hex(32) if is_valid else None,
            "message": "Verification completed" if is_valid else "Verification failed"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to verify solution: {str(e)}")
