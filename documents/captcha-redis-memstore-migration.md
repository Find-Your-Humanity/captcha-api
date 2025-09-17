# Redis MemStore 마이그레이션 가이드 (captcha-api)

작성일: 2025-09-03
대상 서비스: backend/captcha-api (FastAPI)
목적: 파드 로컬 in-memory 세션 의존 제거, KakaoCloud Redis MemStore(Managed)로 중앙 세션 저장 전환

---

## 0. 요약(Executive Summary)
- 왜: 현재 파드 로컬 메모리에 캡차 세션을 저장하여 라우팅이 다른 파드로 가면 `Challenge not found` 오류가 발생.
- 무엇을: Redis MemStore를 세션 저장소로 도입해 모든 파드가 동일한 세션을 조회/검증하도록 통일.
- 어떻게: main.py의 전역 dict(ABSTRACT_SESSIONS, IMAGE_GRID_SESSIONS 등) 참조 지점을 Redis read/write로 치환. TTL, 시도 횟수, 페이로드를 키 단위로 관리.
- 롤아웃: 기능 플래그(USE_REDIS)로 점진 적용 → 스테이징 검증 → 프로덕션 전환.

---

## 1. 접속 정보 및 요구 환경
- Redis Endpoint: team1-redis.1bb3c9ceb1db43928600b93b2a2b1d50.redis.managed-service.kr-central-2.kakaocloud.com
- Port: 6397
- 엔진 버전: Redis 7.2.7
- 라이선스: OSS (Open Source Software)
- 사용자 인증: 미사용
- 전송 암호화(TLS): 미사용
- 파라미터 그룹: Redis.7.2.Default.Cluster (In-Sync)

필요 패키지(서버):
- redis-py 5.x (sync 또는 asyncio 중 선택; 현재 captcha-api는 sync 경로 사용 중이므로 sync 예제 우선)

PowerShell(개발 PC)에서 설치:
- uv 또는 pip 중 택1
- pip 사용 시: `pip install redis~=5.0`

---

## 2. 환경변수 정의(.env / K8s Secret)
다음 변수를 captcha-api 컨테이너에 주입하세요.

- USE_REDIS=true
- REDIS_HOST=team1-redis.1bb3c9ceb1db43928600b93b2a2b1d50.redis.managed-service.kr-central-2.kakaocloud.com
- REDIS_PORT=6397
- REDIS_DB=0
- REDIS_SSL=false                (현재 환경: TLS 미사용)
- REDIS_PREFIX=rcaptcha:         (키 네임스페이스 프리픽스)
- REDIS_TIMEOUT_MS=2000          (연결/응답 타임아웃)
- (인증 미사용 환경: REDIS_PASSWORD 미설정)

프로덕션 예시(.env.production 또는 K8s Secret/ConfigMap 조합):

```
USE_REDIS=true
REDIS_HOST=team1-redis.1bb3c9ceb1db43928600b93b2a2b1d50.redis.managed-service.kr-central-2.kakaocloud.com
REDIS_PORT=6397
REDIS_DB=0
REDIS_SSL=false
REDIS_PREFIX=rcaptcha:
REDIS_TIMEOUT_MS=2000
```

Kubernetes 설정 예시(manifests 참고):
- ConfigMap(captcha-api-config): USE_REDIS, REDIS_* 값들
- (현재 환경은 인증 미사용이므로 Secret 불필요)

---

## 3. 키 설계(Key Schema)
키는 모두 프리픽스 적용: `${REDIS_PREFIX}<type>:<challenge_id>`

1) Abstract 캡차
- Key: rcaptcha:abstract:<cid>
- Type: String(JSON 직렬화)
- TTL: 60초 (생성 시 setex)
- JSON 구조 예:
  ```json
  {
    "type": "abstract",
    "cid": "<challenge_id>",
    "target_class": "apple",
    "keywords": ["사과"],
    "image_urls": ["https://...", "..."],
    "is_positive": [true, false, ...],
    "attempts": 0,
    "created_at": 1690000000
  }
  ```

2) Image Grid(3x3) 캡차
- Key: rcaptcha:imagegrid:<cid>
- Type: String(JSON)
- TTL: 60초
- JSON 구조 예:
  ```json
  {
    "type": "imagegrid",
    "cid": "<challenge_id>",
    "image_url": "https://...",
    "attempts": 0,
    "created_at": 1690000000
  }
  ```

3) Handwriting 캡차 (전역 상태 제거)
- Key: rcaptcha:handwriting:<cid>
- Type: String(JSON)
- TTL: 60초
- JSON 구조 예:
  ```json
  {
    "type": "handwriting",
    "cid": "<challenge_id>",
    "samples": ["https://..."],
    "target_class": "apple",
    "attempts": 0,
    "created_at": 1690000000
  }
  ```

Note: attempts는 원자적 증가(INCR) 또는 JSON 재저장 시 정합성 주의.

---

## 4. 코드 변경 가이드(Calculated minimal changes; Cursor 친화)
아래는 sync redis 클라이언트 사용 예시입니다. main.py 기준 검색 포인트와 교체 스니펫을 제공합니다.

### 4.1 공통: Redis 클라이언트/헬퍼 추가
검색: `# CORS 설정` 위쪽 유틸 영역에 다음 블록을 추가합니다.

```python
# ===== Redis MemStore =====
import json as _json
import redis

USE_REDIS = os.getenv("USE_REDIS", "false").lower() == "true"
REDIS_HOST = os.getenv("REDIS_HOST")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")
REDIS_SSL = os.getenv("REDIS_SSL", "false").lower() == "true"
REDIS_PREFIX = os.getenv("REDIS_PREFIX", "rcaptcha:")
REDIS_TIMEOUT_MS = int(os.getenv("REDIS_TIMEOUT_MS", "2000"))

_redis_client = None

def get_redis():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    if not USE_REDIS:
        return None
    try:
        _redis_client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=REDIS_DB,
            password=REDIS_PASSWORD,
            ssl=REDIS_SSL,
            socket_connect_timeout=REDIS_TIMEOUT_MS / 1000.0,
            socket_timeout=REDIS_TIMEOUT_MS / 1000.0,
            decode_responses=True,
        )
        # ping으로 연결 테스트
        _redis_client.ping()
        return _redis_client
    except Exception as e:
        print(f"⚠️ Redis connect failed: {e}")
        _redis_client = None
        return None

# JSON set/get helpers

def rkey(*parts: str) -> str:
    return REDIS_PREFIX + ":".join([p.strip(":") for p in parts if p])

def redis_set_json(key: str, value: dict, ttl: int):
    r = get_redis()
    if not r:
        return False
    data = _json.dumps(value, ensure_ascii=False)
    return r.setex(key, ttl, data)

def redis_get_json(key: str) -> dict | None:
    r = get_redis()
    if not r:
        return None
    data = r.get(key)
    if not data:
        return None
    try:
        return _json.loads(data)
    except Exception:
        return None

def redis_del(key: str):
    r = get_redis()
    if not r:
        return 0
    try:
        return r.delete(key)
    except Exception:
        return 0

def redis_incr_attempts(key: str, field: str = "attempts", ttl: int | None = None) -> int:
    r = get_redis()
    if not r:
        return -1
    with r.pipeline() as p:
        # 단일 JSON 값이므로 GET -> PARSE -> INC -> SETEX
        val = redis_get_json(key) or {}
        cur = int(val.get(field, 0)) + 1
        val[field] = cur
        if ttl is None:
            # 현재 남은 TTL 유지
            try:
                remain = r.ttl(key)
                if remain and remain > 0:
                    p.setex(key, remain, _json.dumps(val, ensure_ascii=False))
                else:
                    p.set(key, _json.dumps(val, ensure_ascii=False))
            except Exception:
                p.set(key, _json.dumps(val, ensure_ascii=False))
        else:
            p.setex(key, ttl, _json.dumps(val, ensure_ascii=False))
        p.execute()
        return cur
```

### 4.2 Abstract: 세션 쓰기(생성) 치환
검색: `# 세션 저장` 이후 challenge_id 생성 뒤 in-memory 저장하는 위치
치환: Redis에 JSON 저장 (in-memory는 옵션 유지)

```python
challenge_id = uuid.uuid4().hex
ttl_seconds = 60

session_doc = {
    "type": "abstract",
    "cid": challenge_id,
    "target_class": target_class,
    "keywords": keywords,
    "image_urls": [img.get("url", "") for img in images],
    "is_positive": is_positive_flags,
    "attempts": 0,
    "created_at": time.time(),
}

if USE_REDIS and get_redis():
    key = rkey("abstract", challenge_id)
    ok = redis_set_json(key, session_doc, ttl_seconds)
    if not ok:
        print("⚠️ failed to write abstract session to Redis; fallback to memory")
else:
    # 기존 in-memory 경로 유지
    with ABSTRACT_SESSIONS_LOCK:
        ABSTRACT_SESSIONS[challenge_id] = session
```

### 4.3 Abstract: 세션 읽기/검증 치환
검색: `verify_abstract_captcha` 내 `session = ABSTRACT_SESSIONS.get(req.challenge_id)`
치환(분기 도입):

```python
if USE_REDIS and get_redis():
    key = rkey("abstract", req.challenge_id)
    doc = redis_get_json(key)
    if not doc:
        return {"success": False, "message": "Challenge not found"}
    # 만료는 Redis TTL로 관리되므로 별도 검사 생략 가능
    selections_set = set(req.selections or [])
    positives_set = {i for i, flag in enumerate(doc.get("is_positive", [])) if flag}
    tp = sum(1 for i in positives_set if i in selections_set)
    fp = sum(1 for i in selections_set if i not in positives_set)
    fn = sum(1 for i in positives_set if i not in selections_set)
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    img_score = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    is_pass = positives_set == selections_set
    attempts = redis_incr_attempts(key)
    if is_pass or (attempts >= 2 and attempts >= 0):
        redis_del(key)
    return {
        "success": is_pass,
        "img_score": round(img_score, 4),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "target_class": doc.get("target_class"),
        "keywords": doc.get("keywords", []),
        "attempts": attempts if isinstance(attempts, int) and attempts >= 0 else None,
        "expired": False,
        "downshift": (not is_pass and isinstance(attempts, int) and attempts >= 2) or None,
    }
else:
    # 기존 in-memory 경로 유지 (현행 코드)
```

### 4.4 Image Grid: 생성/검증 치환
- 생성(`/api/imagecaptcha-challenge`): challenge_id 생성 후 아래 JSON을 Redis에 setex
  ```python
  doc = {
      "type": "imagegrid",
      "cid": challenge_id,
      "image_url": url,
      "attempts": 0,
      "created_at": time.time(),
  }
  redis_set_json(rkey("imagegrid", challenge_id), doc, session.ttl_seconds)
  ```
- 검증(`/api/imagecaptcha-verify`): in-memory 대신 Redis에서 image_url 읽어 YOLO 호출, attempts 증가 및 조건부 삭제

### 4.5 Handwriting: 전역 상태 제거(선택 단계)
- 생성 시 challenge_id 부여, target_class/samples를 Redis에 저장하여 반환(기존 전역 대신)
- 검증 시 Redis에서 cid로 target_class 로드해 OCR 결과와 비교

---

## 5. 단계별 롤아웃 플랜
1) 코드에 USE_REDIS 분기 추가 후 배포 (기본값 false) → 기능 비활성 상태에서 정상 동작 확인
2) 스테이징에서 USE_REDIS=true로 배포 → 성공률, 레이턴시, 에러율 모니터링
3) 프로덕션 일부 파드만 USE_REDIS=true(카나리) → 지표 OK 시 전체 전환
4) 안정화 후 in-memory 코드 제거(선택)

---

## 6. 로컬/스테이징 테스트
- 로컬 Redis(Docker) 예시:
  - PowerShell: `docker run -p 6379:6379 --name rc-redis -d redis:7`
  - .env: `REDIS_HOST=localhost`, `REDIS_PORT=6379`, `REDIS_SSL=false`, `USE_REDIS=true`
- Postman/curl 테스트 시나리오:
  1) /api/abstract-captcha 호출 → challenge_id 수신
  2) Redis에서 키 존재 확인 → 값 TTL 확인
  3) /api/abstract-verify 로 오답 시도 2회 → attempts 증가 및 키 삭제 확인

---

## 7. 배포 설정(Kubernetes) 힌트
- Deployment env:
  - envFrom:
    - configMapRef: name: captcha-api-config
  - (현재 환경은 인증 미사용: secretRef 불필요)
- Readiness/Liveness 영향 없음. 네트워크/보안 그룹에서 Redis 아웃바운드 허용 필요.

예시 패치(요지):
- ConfigMap: USE_REDIS, REDIS_HOST/PORT/DB/SSL/REDIS_PREFIX/REDIS_TIMEOUT_MS 추가
- Secret: 없음(현재 환경 인증 미사용)

---

## 8. 모니터링/알람 포인트
- 애플리케이션 로그: `⚠️ Redis connect failed` 빈도, `Challenge not found` 비율 감소 여부
- Redis 지표: 연결 수, 키 수, 만료율, 메모리 사용량, latency(ms)
- API 성공률: /abstract-verify, /imagecaptcha-verify 2xx/4xx/5xx 분포

---

## 9. 트러블슈팅
- 증상: `Challenge not found` 지속 발생
  - 점검: USE_REDIS=true 여부, Redis 연결 로그, 키 TTL이 즉시 만료되었는지(TTL<0), 프리픽스 불일치
- 증상: `ML predict failed`
  - Redis 전환과 무관. YOLO/ML 서비스 연결/응답 문제
- 증상: 연결 실패(Timeout)
  - REDIS_PORT=6397 사용 여부 확인, VPC/보안그룹. 현재 환경은 TLS 미사용(REDIS_SSL=false); 보안 요구 시 TLS 활성화(REDIS_SSL=true)로 전환

---

## 10. 보안 권고
- 현재 환경: 인증 미사용. 향후 인증 활성화 시 비밀번호/ACL은 K8s Secret로만 취급, git에 커밋 금지
- 현재 환경: TLS 미사용(REDIS_SSL=false). 향후 보안 요구 시 TLS(REDIS_SSL=true) 활성화 권장
- 프리픽스 분리로 실수/키 충돌 최소화
- TTL은 반드시 설정(setex)하여 잔존 데이터 최소화

---

## 11. Cursor 전용 빠른 작업 가이드
- 검색 포인트:
  - `ABSTRACT_SESSIONS` 사용 위치
  - `IMAGE_GRID_SESSIONS` 사용 위치
  - `HANDWRITING_CURRENT_CLASS` 직접 참조 위치(선택 교체)
- 추가 코드 블록: "===== Redis MemStore ====="로 식별되는 섹션을 공통 유틸로 삽입
- 단계:
  1) 공통 Redis 유틸 블록 추가
  2) Abstract 생성/검증 분기 적용
  3) Image Grid 생성/검증 분기 적용
  4) Handwriting는 별도 PR로 분리 가능(챌린지 cid 도입)
- 체크리스트:
  - [ ] USE_REDIS=false에서 리그레션 없음
  - [ ] USE_REDIS=true에서 Redis 키 생성/만료 정상
  - [ ] 시도 횟수 2회 후 키 삭제
  - [ ] 로그/모니터링 포인트 동작

---

부록 A. 간단한 Redis 키 확인 예시
- PowerShell: `redis-cli -h team1-redis...kakaocloud.com -p 6397 keys 'rcaptcha:*' | Select-Object -First 20`
- TTL 확인: `TTL rcaptcha:abstract:<cid>`

부록 B. Python REPL 빠른 점검
```python
import os, redis, json
r = redis.Redis(
    host=os.getenv('REDIS_HOST'),
    port=int(os.getenv('REDIS_PORT')),
    db=int(os.getenv('REDIS_DB','0')),
    ssl=False,
    decode_responses=True
)
print(r.ping())
print(r.setex('rcaptcha:test', 10, json.dumps({'ok':1})))
print(r.get('rcaptcha:test'))
```

이 문서는 KakaoCloud Redis MemStore 도입을 위한 실무 중심 가이드입니다. Cursor로 본 문서를 참고하여 검색-치환 작업을 진행하면 최소 변경으로 안전하게 전환할 수 있습니다.
