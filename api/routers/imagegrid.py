from fastapi import APIRouter, Header, HTTPException
from typing import Any, Dict, List, Optional
import time

from services.imagegrid_service import create_imagegrid_challenge, verify_imagegrid
from schemas.requests import ImageGridVerifyRequest
from utils.usage import track_api_usage
from database import log_request, log_request_to_request_logs, update_daily_api_stats, update_daily_api_stats_by_key
from database import verify_api_key_with_secret, verify_api_key_auto_secret, verify_captcha_token


router = APIRouter()


@router.post("/api/image-challenge")
def create_image_challenge(
    x_api_key: Optional[str] = Header(None),
    x_secret_key: Optional[str] = Header(None),
    user_agent: Optional[str] = Header(None)
) -> Dict[str, Any]:
    start_time = time.time()
    
    # User-Agent 디버깅 로그
    print(f"🔍 [ImageCaptcha] User-Agent: {user_agent}")
    
    # API 키/시크릿 검증 (데모 키는 공개키만으로 허용)
    if not x_api_key:
        raise HTTPException(status_code=401, detail="API key required")
    
    # 데모 키 하드코딩 (홈페이지 데모용)
    DEMO_PUBLIC_KEY = 'rc_live_f49a055d62283fd02e8203ccaba70fc2'
    
    # 데모 키 처리 (환경 변수 DEMO_SECRET_KEY 필요)
    if x_api_key == DEMO_PUBLIC_KEY:
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
    
    try:
        result = create_imagegrid_challenge()
        response_time = int((time.time() - start_time) * 1000)
        
        # API 요청 로그 저장
        if api_key_info and not api_key_info.get('is_demo', False):
            # api_request_logs 테이블에 로그 저장
            log_request(
                user_id=api_key_info['user_id'],
                api_key=x_api_key,
                path="/api/image-challenge",
                api_type="imagecaptcha",
                method="POST",
                status_code=200,
                response_time=response_time
            )
            
            # request_logs 테이블에도 로그 저장
            log_request_to_request_logs(
                user_id=api_key_info['user_id'],
                api_key=x_api_key,
                path="/api/image-challenge",
                api_type="imagecaptcha",
                method="POST",
                status_code=200,
                response_time=response_time,
                user_agent=None
            )
            
            # 일별 통계 업데이트 (전역)
            update_daily_api_stats("imagecaptcha", True, response_time)
            
            # 사용자별 일별 통계 업데이트
            update_daily_api_stats_by_key(
                user_id=api_key_info['user_id'],
                api_key=x_api_key,
                api_type="imagecaptcha",
                response_time=response_time,
                is_success=True
            )
            
            print(f"📝 [/api/image-challenge] 로그 및 통계 저장 완료")
        
        return result
        
    except Exception as e:
        response_time = int((time.time() - start_time) * 1000)
        
        # 에러 로그 저장
        if api_key_info and not api_key_info.get('is_demo', False):
            # api_request_logs 테이블에 에러 로그 저장
            log_request(
                user_id=api_key_info['user_id'],
                api_key=x_api_key,
                path="/api/image-challenge",
                api_type="imagecaptcha",
                method="POST",
                status_code=500,
                response_time=response_time
            )
            
            # request_logs 테이블에도 에러 로그 저장
            log_request_to_request_logs(
                user_id=api_key_info['user_id'],
                api_key=x_api_key,
                path="/api/image-challenge",
                api_type="imagecaptcha",
                method="POST",
                status_code=500,
                response_time=response_time,
                user_agent=None
            )
            
            # 일별 통계 업데이트 (전역) - 실패
            update_daily_api_stats("imagecaptcha", False, response_time)
            
            # 사용자별 일별 통계 업데이트 - 실패
            update_daily_api_stats_by_key(
                user_id=api_key_info['user_id'],
                api_key=x_api_key,
                api_type="imagecaptcha",
                response_time=response_time,
                is_success=False
            )
            
            print(f"📝 [/api/image-challenge] 에러 로그 및 통계 저장 완료")
        
        raise HTTPException(status_code=500, detail=f"Failed to create image challenge: {str(e)}")


@router.post("/api/imagecaptcha-verify")
async def verify_image_grid(
    req: ImageGridVerifyRequest,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_secret_key: Optional[str] = Header(None, alias="X-Secret-Key")
) -> Dict[str, Any]:
    start_time = time.time()
    
    # 1) API 키 검증
    if not x_api_key:
        raise HTTPException(status_code=401, detail="API key required")
    
    # 데모 키 하드코딩 (홈페이지 데모용)
    DEMO_PUBLIC_KEY = 'rc_live_f49a055d62283fd02e8203ccaba70fc2'
    
    if x_api_key == DEMO_PUBLIC_KEY:
        # 데모 키: 공개키만으로 검증 (브라우저에서 직접 호출)
        api_key_info = verify_api_key_auto_secret(x_api_key)
        if not api_key_info or not api_key_info.get('is_demo'):
            raise HTTPException(status_code=401, detail="Invalid demo API key")
        print(f"🎯 데모 모드 캡차 검증: {DEMO_PUBLIC_KEY} 사용")
    else:
        # 일반 키: 공개키+비밀키 검증 (사용자 서버에서 호출)
        if not x_secret_key:
            raise HTTPException(status_code=401, detail="Secret key required for non-demo keys")
        
        api_key_info = verify_api_key_with_secret(x_api_key, x_secret_key)
        if not api_key_info:
            raise HTTPException(status_code=401, detail="Invalid API key or secret key")
        print(f"🔒 일반 모드 캡차 검증: {x_api_key[:20]}... 사용")
    
    # 2) 캡차 토큰 검증
    if not req.captcha_token:
        raise HTTPException(status_code=400, detail="Captcha token required")
    
    token_valid = verify_captcha_token(req.captcha_token, api_key_info['api_key_id'])
    if not token_valid:
        raise HTTPException(status_code=400, detail="Invalid or expired captcha token")
    
    # 3) 이미지 캡차 검증
    result = verify_imagegrid(req.challenge_id, req.selections)
    
    # DB 로깅: 성공/실패 요청
    status_code = 200 if result.get("success") else 400
    await track_api_usage(
        api_key=x_api_key,
        endpoint="/api/imagecaptcha-verify",
        status_code=status_code,
        response_time=int((time.time() - start_time) * 1000)
    )
    
    return result


