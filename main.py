from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Any, Dict
import sys
import os
import json
import tempfile

app = FastAPI()

# ML 서비스 import를 위한 경로 추가
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
sys.path.append(project_root)

# ML 서비스의 봇 탐지 함수 import
try:
    from ml_service.src.behavior_analysis.inference_bot_detector import detect_bot
    ML_SERVICE_AVAILABLE = True
    print("✅ ML 서비스 연결 성공!")
except ImportError as e:
    print(f"⚠️ ML 서비스 연결 실패: {e}")
    print("임시 로직을 사용합니다.")
    ML_SERVICE_AVAILABLE = False


# 요청 모델 정의
class CaptchaRequest(BaseModel):
    behavior_data: Dict[str, Any]

# CORS 설정 추가
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",        # 개발환경 (React 개발 서버)
        "http://localhost:3001",        # 대시보드 개발 서버
        "https://realcatcha.com",       # 프로덕션 프론트엔드 도메인
        "https://www.realcatcha.com", # www 서브도메인
        "https://api.realcatcha.com",
        "https://test.realcatcha.com",  # api 서브도메인
        "https://dashboard.realcatcha.com"  # 대시보드 도메인 
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
    
    if ML_SERVICE_AVAILABLE:
        try:
            # 🤖 실제 ML 모델 사용
            # 행동 데이터를 임시 파일로 저장하여 ML 모델에 전달
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp_file:
                json.dump([behavior_data], tmp_file)  # 리스트 형태로 저장
                tmp_path = tmp_file.name
            
            # ML 모델로 봇 탐지 실행
            detection_result = detect_bot(tmp_path)
            
            # 임시 파일 삭제
            os.unlink(tmp_path)
            
            # ML 결과에서 신뢰도 점수 추출
            if detection_result and 'confidence_score' in detection_result:
                confidence_score = detection_result['confidence_score']
                is_bot = detection_result.get('is_bot', False)
            else:
                # ML 결과가 없으면 기본값
                confidence_score = 50
                is_bot = False
                
            print(f"🤖 ML 분석 결과: 신뢰도={confidence_score}, 봇여부={is_bot}")
            
        except Exception as e:
            print(f"❌ ML 분석 오류: {e}")
            # ML 분석 실패 시 기본값
            confidence_score = 60
            is_bot = False
    else:
        # ML 서비스 없을 때 임시 로직
        confidence_score = 75  # 임시값
        is_bot = False
    
    # 신뢰도에 따른 캡차 타입 결정
    if confidence_score >= 70:
        captcha_type = "none"  # 캡차 없이 통과
        next_captcha = "success"  # 프론트엔드에서 기대하는 값
    elif confidence_score >= 40:
        captcha_type = "image"  # 이미지 캡차
        next_captcha = "imagecaptcha"  # 프론트엔드에서 기대하는 값
    elif confidence_score >= 20:
        captcha_type = "handwriting"  # 필기 캡차
        next_captcha = "handwritingcaptcha"  # 프론트엔드에서 기대하는 값
    else:
        captcha_type = "abstract"  # 추상 캡차
        next_captcha = "abstractcaptcha"  # 프론트엔드에서 기대하는 값
    
    return {
        "message": "Behavior analysis completed",
        "status": "success",
        "confidence_score": confidence_score,
        "captcha_type": captcha_type,
        "next_captcha": next_captcha,  # 프론트엔드가 기대하는 필드명
        "behavior_data_received": len(str(behavior_data)) > 0,
        "ml_service_used": ML_SERVICE_AVAILABLE,
        "is_bot_detected": is_bot if ML_SERVICE_AVAILABLE else None
    }
