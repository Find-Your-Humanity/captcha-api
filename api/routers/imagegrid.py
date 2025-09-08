from fastapi import APIRouter
from typing import Any, Dict, List
import time

from services.imagegrid_service import create_imagegrid_challenge, verify_imagegrid
from schemas.requests import ImageGridVerifyRequest
from utils.usage import track_api_usage


router = APIRouter()


@router.post("/api/image-challenge")
def create_image_challenge() -> Dict[str, Any]:
    return create_imagegrid_challenge()


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


