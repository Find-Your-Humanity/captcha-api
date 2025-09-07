from fastapi import APIRouter, HTTPException
from typing import Any, Dict, List, Optional
import base64, uuid, time
from datetime import datetime
from pathlib import Path
import httpx

from services.handwriting_service import verify_handwriting, create_handwriting_challenge
from schemas.requests import HandwritingVerifyRequest
from config.settings import (
    CAPTCHA_TTL,
    USE_REDIS,
    OCR_API_URL,
    OCR_IMAGE_FIELD,
    DEBUG_SAVE_OCR_UPLOADS,
    DEBUG_OCR_DIR,
    SUCCESS_REDIRECT_URL,
)
from utils.text import normalize_text


router = APIRouter()


@router.post("/api/handwriting-verify")
def verify(req: HandwritingVerifyRequest) -> Dict[str, Any]:
    start_time = time.time()
    # 1) Base64 디코드 (data:image 접두 처리)
    base64_str = req.image_base64 or ""
    if base64_str.startswith("data:image"):
        base64_str = base64_str.split(",", 1)[1]
    try:
        image_bytes = base64.b64decode(base64_str)
    except Exception as e:
        return {"success": False, "message": f"Invalid base64 image: {e}"}

    # 디버그 저장
    if DEBUG_SAVE_OCR_UPLOADS:
        try:
            save_dir = Path(DEBUG_OCR_DIR)
            save_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
            raw_name = f"ocr_upload_raw_{ts}_{uuid.uuid4().hex[:8]}.png"
            fpath_raw = save_dir / raw_name
            with open(fpath_raw, "wb") as fp:
                fp.write(image_bytes)
        except Exception:
            pass

    # 2) OCR API 호출
    if not OCR_API_URL:
        return {"success": False, "message": "OCR_API_URL is not configured on server."}

    def _call_ocr_multipart():
        field = OCR_IMAGE_FIELD or "file"
        files = {field: ("handwriting.png", image_bytes, "image/png")}
        return httpx.post(OCR_API_URL, files=files, timeout=20.0)

    try:
        resp = _call_ocr_multipart()
        resp.raise_for_status()
        ocr_json = resp.json()
    except Exception as e:
        return {"success": False, "message": f"OCR API request failed: {e}"}

    # 3) 텍스트 추출 및 정규화
    extracted = None
    if isinstance(ocr_json, dict):
        extracted = (
            ocr_json.get("text")
            or ocr_json.get("prediction")
            or (ocr_json.get("result", {}) or {}).get("text")
        )
    if not extracted or not isinstance(extracted, str):
        return {"success": False, "message": "OCR API response missing text field"}

    text_norm = normalize_text(extracted)

    # 4) 검증 (세션/시도증가/조건부삭제는 서비스 내부에서 처리)
    result = verify_handwriting(req.challenge_id or "", text_norm, user_id=req.user_id, api_key=req.api_key)
    if result.get("success") and SUCCESS_REDIRECT_URL:
        result["redirect_url"] = SUCCESS_REDIRECT_URL
    return result


@router.post("/api/handwriting-challenge")
async def create_handwriting(x_api_key: Optional[str] = None) -> Dict[str, Any]:
    # 기존 main 로직을 단순화해 전달: 샘플 URL과 타겟 클래스는 main의 상태를 사용하는 곳에서 받아오도록 설계 필요.
    # Phase 3에서는 라우터에서 최소한의 조합만 수행.
    samples: List[str] = []
    target_class = ""
    return create_handwriting_challenge(samples, target_class)


