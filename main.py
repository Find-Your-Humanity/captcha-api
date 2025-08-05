from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

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

@app.get("/api/next-captcha")
def next_captcha():
    return {
        "message": "Captcha API endpoint",
        "status": "success",
        "captcha_type": "image",
        "confidence_score": 50
    }
