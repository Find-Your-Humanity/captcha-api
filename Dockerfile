# 더 작고 빠른 베이스 이미지
FROM python:3.9-slim

# 환경 변수 설정 (파이썬 버퍼링 비활성화)
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 작업 디렉토리 설정
WORKDIR /app

# 종속성 먼저 복사 → Docker 캐시 활용 최적화
COPY requirements.txt .

# 시스템 패키지 최소 설치 및 정리
RUN apt-get update && apt-get install -y --no-install-recommends gcc \
  && pip install --no-cache-dir --upgrade pip \
  && pip install --no-cache-dir -r requirements.txt \
  && apt-get remove -y gcc \
  && apt-get autoremove -y \
  && rm -rf /var/lib/apt/lists/*

# 나머지 애플리케이션 소스 복사
COPY . .

# 실행 명령
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "80"]
