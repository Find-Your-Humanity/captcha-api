from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Any, Dict, Optional
from dotenv import load_dotenv
import httpx
import os
import json
import random
import base64
from io import BytesIO
from pathlib import Path
from datetime import datetime
import uuid
from PIL import Image
try:
    RESAMPLE_LANCZOS = Image.Resampling.LANCZOS  # Pillow >= 9.1
except Exception:
    RESAMPLE_LANCZOS = Image.LANCZOS  # Fallback for older Pillow

# 실행 환경에 따라 .env 파일 분기 로드
ENV = os.getenv("APP_ENV", "development")
if ENV == "production":
    load_dotenv(".env.production")
else:
    load_dotenv(".env.development")

# ML API 서버 주소 (Docker 환경이면 'ml-service', 로컬 개발이면 'localhost')
ML_SERVICE_URL = os.getenv("ML_SERVICE_URL")
# 백엔드 테스트 모드: ML 호출 스킵하고 고정 점수 사용
TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"
HANDWRITING_MANIFEST_PATH = os.getenv("HANDWRITING_MANIFEST_PATH", "handwriting_manifest.json")
SUCCESS_REDIRECT_URL = os.getenv("SUCCESS_REDIRECT_URL")
OCR_API_URL = os.getenv("OCR_API_URL")
OCR_REQUEST_FORMAT = os.getenv("OCR_REQUEST_FORMAT", "multipart").lower()  # 'json' | 'multipart'
OCR_IMAGE_FIELD = os.getenv("OCR_IMAGE_FIELD")  # 포맷별 기본값 적용
DEBUG_SAVE_OCR_UPLOADS = os.getenv("DEBUG_SAVE_OCR_UPLOADS", "false").lower() == "true"
DEBUG_OCR_DIR = os.getenv("DEBUG_OCR_DIR", "debug_uploads")

app = FastAPI()

class CaptchaRequest(BaseModel):
    behavior_data: Dict[str, Any]

class HandwritingVerifyRequest(BaseModel):
    image_base64: str
    # keywords: Optional[str] = None  # 필요 시 활성화

# 전역 상태: 서버 시작 시 1회 로드한 매니페스트와 선택된 챌린지
HANDWRITING_MANIFEST: Dict[str, Any] = {}
HANDWRITING_CURRENT_CLASS: Optional[str] = None
HANDWRITING_CURRENT_IMAGES: list[str] = []


def _normalize_text(text: str) -> str:
    return "".join(ch.lower() for ch in text.strip() if ch.isalnum())


def _load_handwriting_manifest(path: str) -> Dict[str, list[str]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"⚠️ handwriting manifest not found at: {path}")
        return {}
    except Exception as e:
        print(f"⚠️ failed to load handwriting manifest: {e}")
        return {}

    # 허용 포맷 1: {"classes": {"apple": ["...", "..."], ...}}
    if isinstance(data, dict) and "classes" in data and isinstance(data["classes"], dict):
        return {str(k): list(v) for k, v in data["classes"].items()}

    # 허용 포맷 2: 리스트[{"class": "apple", "path": "..."}, ...]
    if isinstance(data, list):
        classes: Dict[str, list[str]] = {}
        for item in data:
            try:
                cls = str(item["class"])
                p = str(item["path"])
            except Exception:
                continue
            classes.setdefault(cls, []).append(p)
        return classes

    print("⚠️ unsupported manifest format; expected {'classes': {...}} or list of {class, path}")
    return {}


def _select_handwriting_challenge() -> None:
    global HANDWRITING_CURRENT_CLASS, HANDWRITING_CURRENT_IMAGES
    if not HANDWRITING_MANIFEST:
        HANDWRITING_CURRENT_CLASS = None
        HANDWRITING_CURRENT_IMAGES = []
        return
    cls = random.choice(list(HANDWRITING_MANIFEST.keys()))
    images = HANDWRITING_MANIFEST.get(cls, [])
    random.shuffle(images)
    HANDWRITING_CURRENT_CLASS = cls
    HANDWRITING_CURRENT_IMAGES = images[:5] if len(images) >= 5 else images


# 서버 시작 시 매니페스트 로드 및 챌린지 선택
HANDWRITING_MANIFEST = _load_handwriting_manifest(HANDWRITING_MANIFEST_PATH)
_select_handwriting_challenge()
try:
    print(
        f"✍️ Handwriting manifest loaded: classes={len(HANDWRITING_MANIFEST.keys()) if HANDWRITING_MANIFEST else 0}, "
        f"current_class={HANDWRITING_CURRENT_CLASS}, samples={len(HANDWRITING_CURRENT_IMAGES)}"
    )
except Exception:
    pass

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "https://realcatcha.com",
        "https://www.realcatcha.com",
        "https://api.realcatcha.com",
        "https://test.realcatcha.com",
        "https://dashboard.realcatcha.com"
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"Hello": "World"}

@app.post("/api/next-captcha")
def next_captcha(request: CaptchaRequest):
    behavior_data = request.behavior_data

    if TEST_MODE:
        # 테스트용 고정 점수 (원하면 30/50/80 등으로 조절하여 단계 테스트)
        confidence_score = 30
        is_bot = False
        ML_SERVICE_USED = False
        print("🧪 TEST_MODE: ML 호출 없이 고정 점수 사용 (confidence=30)")
    else:
        try:
            #ML API 서버에 요청
            response = httpx.post(ML_SERVICE_URL, json={"behavior_data": behavior_data})
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

    # 신뢰도 기반 캡차 타입 결정
    if confidence_score >= 70:
        captcha_type = "handwriting"
        next_captcha = "handwritingcaptcha"
    elif confidence_score >= 40:
        captcha_type = "handwriting"
        next_captcha = "handwritingcaptcha"
    elif confidence_score >= 20:
        captcha_type = "handwriting"
        next_captcha = "handwritingcaptcha"
    else:
        captcha_type = "handwriting"
        next_captcha = "handwritingcaptcha"

    payload: Dict[str, Any] = {
        "message": "Behavior analysis completed",
        "status": "success",
        "confidence_score": confidence_score,
        "captcha_type": captcha_type,
        "next_captcha": next_captcha,
        "behavior_data_received": len(str(behavior_data)) > 0,
        "ml_service_used": ML_SERVICE_USED,
        "is_bot_detected": is_bot if ML_SERVICE_USED else None
    }

    # handwriting 단계 진입 시 프런트에 샘플 이미지 전달 (정답 텍스트는 서버에만 보관)
    if next_captcha == "handwritingcaptcha":
        payload["handwriting_samples"] = HANDWRITING_CURRENT_IMAGES

    return payload


@app.post("/api/verify-handwriting")
def verify_handwriting(request: HandwritingVerifyRequest):
    # data:image/png;base64,.... 형태 처리
    base64_str = request.image_base64 or ""
    if base64_str.startswith("data:image"):
        base64_str = base64_str.split(",", 1)[1]
    # multipart 전송 대비 원본 바이트도 확보
    try:
        image_bytes = base64.b64decode(base64_str)
    except Exception as e:
        try:
            print(f"⚠️ base64 decode failed: {e}")
        except Exception:
            pass
        return {"success": False, "message": f"Invalid base64 image: {e}"}

    # 전처리 제거: 원본 이미지를 그대로 사용

    # 디버그: 전송 전에 실제 파일로 저장하여 확인
    if DEBUG_SAVE_OCR_UPLOADS:
        try:
            save_dir = Path(DEBUG_OCR_DIR)
            save_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
            raw_name = f"ocr_upload_raw_{ts}_{uuid.uuid4().hex[:8]}.png"
            fpath_raw = save_dir / raw_name
            with open(fpath_raw, "wb") as fp:
                fp.write(image_bytes)
            print(f"💾 Saved OCR upload (raw):  {fpath_raw.resolve()}")
        except Exception as e:
            print(f"⚠️ failed to save debug OCR upload: {e}")

    # 저장까지는 항상 수행하고, 그 다음 설정 검증
    if not OCR_API_URL:
        try:
            print("⚠️ verify-handwriting aborted after save: OCR_API_URL not configured")
        except Exception:
            pass
        return {"success": False, "message": "OCR_API_URL is not configured on server."}

    if not HANDWRITING_CURRENT_CLASS:
        try:
            print("⚠️ verify-handwriting aborted after save: HANDWRITING_CURRENT_CLASS is None (manifest missing or empty)")
        except Exception:
            pass
        return {"success": False, "message": "No handwriting challenge is prepared."}

    def _call_ocr(mode: str):
        field = OCR_IMAGE_FIELD
        if not field:
            field = "image_base64" if mode == "json" else "file"

        print(f"🔎 Calling OCR API: {OCR_API_URL} mode={mode}, field={field}, payloadLen={len(base64_str)}")
        if mode == "multipart":
            files = {field: ("handwriting.png", image_bytes, "image/png")}
            return httpx.post(OCR_API_URL, files=files, timeout=20.0)
        else:
            # JSON으로 보낼 때도 원본 바이트를 base64로 인코딩하여 전송
            body_b64 = base64.b64encode(image_bytes).decode("ascii")
            body = {field: body_b64}
            return httpx.post(OCR_API_URL, json=body, timeout=20.0)

    ocr_json = None
    first_mode = OCR_REQUEST_FORMAT if OCR_REQUEST_FORMAT in ("json", "multipart") else "json"
    second_mode = "multipart" if first_mode == "json" else "json"

    # 1차 시도
    try:
        resp = _call_ocr(first_mode)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            # 실패 로그 상세 출력
            body_preview = e.response.text
            if len(body_preview) > 500:
                body_preview = body_preview[:500] + "... (truncated)"
            print(f"❌ OCR API {first_mode} failed: status={e.response.status_code}, body={body_preview}")
            raise
        ocr_json = resp.json()
    except Exception:
        # 2차 대체 포맷으로 재시도
        try:
            resp = _call_ocr(second_mode)
            resp.raise_for_status()
            ocr_json = resp.json()
            print(f"🔁 Fallback to {second_mode} succeeded")
        except Exception as e2:
            try:
                # 가능한 상세 에러 로그
                if isinstance(e2, httpx.HTTPStatusError):
                    body_preview = e2.response.text
                    if len(body_preview) > 500:
                        body_preview = body_preview[:500] + "... (truncated)"
                    print(f"❌ OCR API {second_mode} failed: status={e2.response.status_code}, body={body_preview}")
                else:
                    print(f"❌ OCR API request failed: {e2}")
            except Exception:
                pass
            return {"success": False, "message": f"OCR API request failed: {e2}"}

    # 로그에 과도한 출력 방지: 앞부분만 표시
    preview = str(ocr_json)
    if len(preview) > 500:
        preview = preview[:500] + "... (truncated)"
    print(f"📦 OCR API response: {preview}")

    # 응답에서 텍스트 추출 (text | prediction | result.text 지원)
    extracted = None
    if isinstance(ocr_json, dict):
        extracted = (
            ocr_json.get("text")
            or ocr_json.get("prediction")
            or (ocr_json.get("result", {}) or {}).get("text")
        )
    if not extracted or not isinstance(extracted, str):
        try:
            print(f"⚠️ OCR response missing text. keys={list(ocr_json.keys()) if isinstance(ocr_json, dict) else 'n/a'}")
        except Exception:
            pass
        return {"success": False, "message": "OCR API response missing text field"}

    # 디버그 로그: OCR에서 받은 원본 텍스트 출력
    try:
        print(f"📝 OCR API text: {extracted}")
    except Exception:
        pass

    extracted_norm = _normalize_text(extracted)
    answer_norm = _normalize_text(HANDWRITING_CURRENT_CLASS)
    is_match = extracted_norm == answer_norm and len(answer_norm) > 0
    try:
        print(f"🧮 normalize: extracted='{extracted_norm}', answer='{answer_norm}', match={is_match}")
    except Exception:
        pass

    response: Dict[str, Any] = {"success": is_match}
    if is_match and SUCCESS_REDIRECT_URL:
        response["redirect_url"] = SUCCESS_REDIRECT_URL
    return response