# Real Captcha API

Real Captcha 시스템의 **메인 백엔드 API 서비스**입니다. 실시간 사용자 행동 분석, ML 기반 봇 탐지, 그리고 적응형 캡차 결정을 담당합니다.

## 🚀 **주요 기능**

### **🤖 실시간 AI 분석**
- **ML 모델 통합**: `ml-service`의 AutoEncoder 봇 탐지 모델과 직접 연동
- **행동 데이터 분석**: 마우스, 클릭, 스크롤 패턴 실시간 처리
- **신뢰도 계산**: 0-100점 스코어 기반 사용자 신뢰도 측정

### **🎯 적응형 캡차 시스템**
- **동적 난이도 조절**: 신뢰도 점수에 따른 캡차 타입 자동 결정
- **4단계 적응형 응답**:
  - 70+ → 캡차 없이 통과
  - 40-69 → 이미지 캡차
  - 20-39 → 필기 캡차  
  - 20미만 → 추상 캡차

### **🌐 프론트엔드 연동**
- **CORS 완전 지원**: 개발/프로덕션 환경 모든 도메인 허용
- **RESTful API**: JSON 기반 요청/응답 처리
- **실시간 통신**: 사용자 행동 즉시 분석 및 응답

## 🏗️ **프로젝트 구조**

```
backend/captcha-api/
├── main.py                 # 메인 FastAPI 애플리케이션
├── requirements.txt        # Python 의존성 패키지
├── Dockerfile             # 컨테이너 배포 설정
├── README.md              # 이 파일
└── src/                   # 추가 모듈 (향후 확장)
    └── __init__.py
```

## 🔗 **ML 서비스 연동**

### **AutoEncoder 모델 통합**
```python
# ml-service의 봇 탐지 함수 직접 import
from ml_service.src.behavior_analysis.inference_bot_detector import detect_bot

# 실시간 ML 분석
detection_result = detect_bot(behavior_data_file)
confidence_score = detection_result['confidence_score']
```

### **Fallback 시스템**
- **ML 서비스 연결 성공**: 실제 AutoEncoder 모델 사용 🤖
- **ML 서비스 연결 실패**: 임시 로직으로 안전하게 동작 🔄

## 📡 **API 엔드포인트**

### **POST /api/next-captcha**

실시간 사용자 행동 분석 및 적응형 캡차 결정

#### 요청 (Request)
```json
{
  "behavior_data": {
    "mouseMovements": [
      {"x": 100, "y": 200, "timestamp": 1672531200000}
    ],
    "mouseClicks": [
      {"x": 150, "y": 250, "timestamp": 1672531201000, "type": "click"}
    ],
    "scrollEvents": [
      {"position": 100, "timestamp": 1672531202000}
    ],
    "pageEvents": {
      "enterTime": 1672531200000,
      "exitTime": 1672531210000
    }
  }
}
```

#### 응답 (Response)
```json
{
  "message": "Behavior analysis completed",
  "status": "success",
  "confidence_score": 85,
  "captcha_type": "image",
  "next_captcha": "imagecaptcha",
  "behavior_data_received": true,
  "ml_service_used": true,
  "is_bot_detected": false
}
```

#### 적응형 응답 값
| **신뢰도** | **captcha_type** | **next_captcha** | **사용자 경험** |
|------------|------------------|------------------|-----------------|
| 70+ | `"none"` | `"success"` | 즉시 통과 ✅ |
| 40-69 | `"image"` | `"imagecaptcha"` | 이미지 선택 🖼️ |
| 20-39 | `"handwriting"` | `"handwritingcaptcha"` | 손글씨 입력 ✍️ |
| <20 | `"abstract"` | `"abstractcaptcha"` | 추상 패턴 🎨 |

### **GET /**

서버 상태 확인

#### 응답
```json
{
  "Hello": "World"
}
```

## 🚀 **빠른 시작**

### **1. 환경 설정**

#### Python 가상환경 생성
```bash
cd backend/captcha-api
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
```

#### 의존성 설치
```bash
pip install -r requirements.txt
```

**주요 패키지:**
- `fastapi` - 고성능 웹 프레임워크
- `uvicorn` - ASGI 서버
- `pydantic` - 데이터 검증
- `torch` - ML 모델 (AutoEncoder)
- `scikit-learn` - 데이터 전처리
- `pandas`, `numpy` - 데이터 분석
- `joblib` - 모델 직렬화

### **2. 서버 실행**

#### 개발 서버
```bash
uvicorn main:app --reload --port 8000
```

#### 프로덕션 서버
```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

#### 실행 확인
```bash
# 서버 상태 확인
curl http://localhost:8000/

# API 테스트
curl -X POST http://localhost:8000/api/next-captcha \
  -H "Content-Type: application/json" \
  -d '{"behavior_data": {"test": true}}'
```

## 🌐 **CORS 설정**

### **허용된 도메인**
```python
allow_origins=[
    "http://localhost:3000",        # 캡차 위젯 개발 서버
    "http://localhost:3001",        # 대시보드 개발 서버
    "https://realcatcha.com",       # 프로덕션 프론트엔드
    "https://www.realcatcha.com",   # www 서브도메인
    "https://api.realcatcha.com",   # API 자체 도메인
    "https://test.realcatcha.com",  # 테스트 환경
    "https://dashboard.realcatcha.com"  # 대시보드 도메인
]
```

### **지원 메서드**
- `GET`, `POST`, `PUT`, `DELETE`, `OPTIONS`
- 모든 헤더 허용 (`*`)
- Credentials 지원 (`allow_credentials=True`)

## 🔧 **기술 스택**

### **웹 프레임워크**
- **FastAPI**: 고성능 비동기 웹 프레임워크
- **Uvicorn**: ASGI 서버
- **Pydantic**: 자동 데이터 검증 및 직렬화

### **ML/AI 통합**
- **PyTorch**: AutoEncoder 모델 추론
- **Scikit-learn**: 데이터 전처리 및 정규화
- **Pandas/NumPy**: 행동 데이터 분석

### **데이터 처리**
- **JSON**: 구조화된 행동 데이터 처리
- **Tempfile**: 안전한 임시 파일 관리
- **실시간 분석**: 동기/비동기 처리 최적화

## 🧪 **테스트 및 디버깅**

### **로컬 테스트 환경**

1. **백엔드 실행**: `uvicorn main:app --reload --port 8000`
2. **프론트엔드 실행**: `npm start` (포트 3000)
3. **브라우저 접속**: `http://localhost:3000`

### **API 직접 테스트**
```bash
# 기본 응답 테스트
curl http://localhost:8000/

# 행동 분석 테스트
curl -X POST http://localhost:8000/api/next-captcha \
  -H "Content-Type: application/json" \
  -d '{
    "behavior_data": {
      "mouseMovements": [{"x": 100, "y": 200, "timestamp": 1672531200000}],
      "mouseClicks": [{"x": 150, "y": 250, "timestamp": 1672531201000}]
    }
  }'
```

### **예상 로그**
```bash
# ML 서비스 연결 성공 시
✅ ML 서비스 연결 성공!
🤖 ML 분석 결과: 신뢰도=85, 봇여부=False

# ML 서비스 연결 실패 시  
⚠️ ML 서비스 연결 실패: No module named 'ml_service'
임시 로직을 사용합니다.
```

## 🐳 **Docker 배포**

### **Dockerfile 사용**
```bash
# 이미지 빌드
docker build -t captcha-api .

# 컨테이너 실행
docker run -p 8000:8000 captcha-api
```

### **환경변수 설정**
```bash
# 커스텀 설정으로 실행
docker run -p 8000:8000 \
  -e MODEL_DIR=/custom/models \
  -e DATA_DIR=/custom/data \
  captcha-api
```

## 🔧 **ML 서비스 연동 상세**

### **import 경로 설정**
```python
# 프로젝트 루트를 Python path에 추가
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
sys.path.append(project_root)

# ML 서비스 import
from ml_service.src.behavior_analysis.inference_bot_detector import detect_bot
```

### **데이터 전달 방식**
1. **행동 데이터 수신**: Pydantic 모델로 검증
2. **임시 파일 생성**: JSON 형태로 저장
3. **ML 모델 호출**: `detect_bot(file_path)` 실행
4. **결과 처리**: 신뢰도 점수 추출
5. **임시 파일 정리**: 자동 삭제

### **에러 처리**
- **Import 실패**: 임시 로직으로 fallback
- **모델 실행 오류**: 기본 신뢰도 점수 사용
- **파일 처리 오류**: 안전한 cleanup 보장

## 🔮 **향후 개발 계획**

### **Phase 1: 성능 최적화**
- 비동기 ML 모델 호출
- 배치 처리 지원
- 캐싱 시스템 도입

### **Phase 2: 확장 기능**
- 사용자별 학습 데이터 저장
- A/B 테스트 프레임워크
- 실시간 모델 업데이트

### **Phase 3: 모니터링**
- Prometheus 메트릭 수집
- Grafana 대시보드 연동
- 성능 모니터링 및 알림

## 🔒 **보안 및 성능**

### **보안 기능**
- CORS 정책 엄격 적용
- 요청 데이터 검증 (Pydantic)
- 임시 파일 안전 처리
- SQL Injection 방지 (NoSQL 사용)

### **성능 최적화**
- 비동기 요청 처리
- ML 모델 메모리 효율성
- 가비지 컬렉션 최적화

## 📄 **라이선스**

MIT License - 자세한 내용은 `LICENSE` 파일을 참조하세요.

---

**Real Captcha API v2.0.0**  
© 2025 Find Your Humanity. All rights reserved.
