from fastapi import APIRouter
from typing import Any, Dict, List

from services.imagegrid_service import create_imagegrid_challenge, verify_imagegrid
from schemas.requests import ImageGridVerifyRequest


router = APIRouter()


@router.post("/api/image-challenge")
def create_image_challenge() -> Dict[str, Any]:
    return create_imagegrid_challenge()


@router.post("/api/imagecaptcha-verify")
def verify_image_grid(req: ImageGridVerifyRequest) -> Dict[str, Any]:
    return verify_imagegrid(req.challenge_id, req.selections)


