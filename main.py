from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Any, Dict
from dotenv import load_dotenv
import httpx
import os

# 실행 환경에 따라 .env 파일 분기 로드
ENV = os.getenv("APP_ENV", "development")
if ENV == "production":
    load_dotenv(".env.production")
else:
    load_dotenv(".env.development")

# ML API 서버 주소 (Docker 환경이면 'ml-service', 로컬 개발이면 'localhost')
ML_SERVICE_URL = os.getenv("ML_SERVICE_URL")

# 테스트 모드: 70점 이상이어도 캡차를 순환하며 모두 표시
TEST_MODE = os.getenv("TEST_MODE", "true").lower() == "true"

# 테스트 모드용 캡차 순환 설정 (image -> handwriting -> abstract)
CAPTCHA_CYCLE_ORDER = ["imagecaptcha", "handwritingcaptcha", "abstractcaptcha"]
CAPTCHA_CYCLE_INDEX = 0

app = FastAPI()

class CaptchaRequest(BaseModel):
    behavior_data: Dict[str, Any]

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
    global CAPTCHA_CYCLE_INDEX
    behavior_data = request.behavior_data

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
        if TEST_MODE:
            # 테스트 모드: 70점 이상이어도 캡차를 순환 표시
            next_captcha = CAPTCHA_CYCLE_ORDER[CAPTCHA_CYCLE_INDEX]
            if next_captcha == "imagecaptcha":
                captcha_type = "image"
            elif next_captcha == "handwritingcaptcha":
                captcha_type = "handwriting"
            else:
                captcha_type = "abstract"
            CAPTCHA_CYCLE_INDEX = (CAPTCHA_CYCLE_INDEX + 1) % len(CAPTCHA_CYCLE_ORDER)
        else:
            captcha_type = "none"
            next_captcha = "success"
    elif confidence_score >= 40:
        captcha_type = "image"
        next_captcha = "imagecaptcha"
    elif confidence_score >= 20:
        captcha_type = "handwriting"
        next_captcha = "handwritingcaptcha"
    else:
        captcha_type = "abstract"
        next_captcha = "abstractcaptcha"

    return {
        "message": "Behavior analysis completed",
        "status": "success",
        "confidence_score": confidence_score,
        "captcha_type": captcha_type,
        "next_captcha": next_captcha,
        "behavior_data_received": len(str(behavior_data)) > 0,
        "ml_service_used": ML_SERVICE_USED,
        "is_bot_detected": is_bot if ML_SERVICE_USED else None
    }