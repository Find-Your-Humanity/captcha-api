from fastapi import APIRouter, Header, HTTPException
from typing import Any, Dict, List, Optional
import time

from services.imagegrid_service import create_imagegrid_challenge, verify_imagegrid
from schemas.requests import ImageGridVerifyRequest
from utils.usage import track_api_usage
from database import verify_api_key, log_request, log_request_to_request_logs, update_daily_api_stats, update_daily_api_stats_by_key


router = APIRouter()


@router.post("/api/image-challenge")
def create_image_challenge(
    x_api_key: Optional[str] = Header(None)
) -> Dict[str, Any]:
    start_time = time.time()
    
    # API 키 검증
    if not x_api_key:
        raise HTTPException(status_code=401, detail="API key required")
    
    # 데모 키 하드코딩 (홈페이지 데모용)
    DEMO_PUBLIC_KEY = 'rc_live_f49a055d62283fd02e8203ccaba70fc2'
    
    # 데모 키인 경우 자동으로 처리
    if x_api_key == DEMO_PUBLIC_KEY:
        api_key_info = {
            'key_id': 'demo',
            'api_key_id': 'demo',
            'user_id': 6,
            'is_demo': True
        }
    else:
        # 일반 API 키 검증
        api_key_info = verify_api_key(x_api_key)
        if not api_key_info:
            raise HTTPException(status_code=401, detail="Invalid API key")
    
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
async def verify_image_grid(req: ImageGridVerifyRequest) -> Dict[str, Any]:
    start_time = time.time()
    
    result = verify_imagegrid(req.challenge_id, req.selections)
    
    # DB 로깅: 성공/실패 요청
    if req.api_key:
        status_code = 200 if result.get("success") else 400
        await track_api_usage(
            api_key=req.api_key,
            endpoint="/api/imagecaptcha-verify",
            status_code=status_code,
            response_time=int((time.time() - start_time) * 1000)
        )
    
    return result


