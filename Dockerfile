# 더 작고 빠른 베이스 이미지
FROM python:3.9-slim

# 환경 변수 설정 (파이썬 버퍼링 비활성화)
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 작업 디렉토리 설정
WORKDIR /app

# 종속성 먼저 복사 → Docker 캐시 활용 최적화
COPY requirements.txt .

    # 시스템 패키지 설치 및 Python 종속성 설치를 한 번에 처리
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        libc6-dev \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && apt-get remove -y gcc g++ libc6-dev \
    && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /tmp/* \
    && rm -rf /var/tmp/*

# 애플리케이션 소스 복사 (최소한의 파일만)
COPY main.py .
COPY src/ ./src/

# 포트 노출
EXPOSE 80

# 헬스체크 추가
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:80/health || exit 1

# 실행 명령
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "80"]
