# 캡차 세션(in-memory) 의존성 분석 및 대안 제안

작성일: 2025-09-03

본 문서는 `backend/captcha-api/main.py`를 기반으로 현재 캡차 흐름에서 발생하는 "Challenge not found" 문제의 원인과, 세션 어피니티 없이 멀티 파드 환경에서 안정적으로 동작하도록 하기 위한 설계 대안을 정리합니다.

---

## 1. 현재 구조 요약과 문제 원인

아래 라인 범위는 `backend/captcha-api/main.py`를 기준으로 합니다.

### 1.1 Abstract 캡차
- 세션 자료구조: `AbstractCaptchaSession` (lines 94–105)
- 전역 저장소: `ABSTRACT_SESSIONS` + `ABSTRACT_SESSIONS_LOCK` (lines 110–112)
- 생성 엔드포인트: `/api/abstract-captcha` (lines 925–1114)
  - 9개의 이미지 후보와 `is_positive` 플래그 배열을 생성 후, `challenge_id`를 키로 메모리에 저장 (lines 1068–1083)
- 검증 엔드포인트: `/api/abstract-verify` (lines 1116–1182)
  - 메모리에서 `challenge_id`로 세션 조회 (lines 1118–1121)
  - 세션이 없으면 `Challenge not found` (line 1121)
  - 선택적 `signatures`는 이미지 인덱스 무결성 확인용으로만 사용되며, 정답 복원에는 미사용 (lines 1130–1136)

문제점: 파드 A에서 생성된 세션은 파드 A의 메모리에만 존재합니다. 검증 요청이 파드 B로 라우팅되면 세션 미존재로 `Challenge not found`.

### 1.2 Handwriting 캡차
- 전역 상태: `HANDWRITING_CURRENT_CLASS`, `HANDWRITING_CURRENT_IMAGES` (lines 81–85)
- 생성 엔드포인트: `/api/handwriting-challenge` (lines 882–921)
  - challenge_id 미발급, 전역 현재 클래스/샘플 목록만 갱신해서 URL 반환
- 검증 엔드포인트: `/api/handwriting-verify` (lines 772–880)
  - OCR 결과를 전역 `HANDWRITING_CURRENT_CLASS`와 비교 (lines 869–877)

문제점: 파드 간 전역 상태가 다를 수 있어 동일 사용자의 검증이 다른 파드로 가면 판정이 어긋납니다. 세션 id가 없어 요청 연계성도 낮습니다.

### 1.3 ImageCaptcha(3x3) 캡차
- 세션 자료구조: `ImageGridCaptchaSession` (lines 1185–1196)
- 전역 저장소: `IMAGE_GRID_SESSIONS` (lines 1199–1201)
- 생성: `/api/imagecaptcha-challenge` (lines 1240–1268)
- 검증: `/api/imagecaptcha-verify` (lines 1276–1357)
  - 세션이 없으면 `Challenge not found` (line 1281)

문제점: Abstract와 동일하게 파드 로컬 메모리 의존.

### 결론(Q1에 대한 판단)
- "현 구조"에서는 in-memory 세션/전역 정보가 필수처럼 쓰이고 있으나, 멀티 파드·무스티키 환경에 부적합합니다.
- 정답 산출 AI 로직을 제외하고 "정답을 MongoDB에 미리 저장"한다면, 메모리 세션 의존을 제거하거나 최소화하는 방향이 권장됩니다.

---

## 2. 대안 설계

두 가지 현실적인 방향이 있습니다.

### 대안 A. 중앙 저장(Distributed Store) 기반 세션
- 개요: 세션을 메모리가 아니라 공용 저장소(MongoDB 또는 Redis)에 TTL과 함께 저장합니다.
- 흐름(공통):
  1) 생성 시 challenge 문서/레코드 생성: { _id: challenge_id, type, payload(이미지 키/URL, target, is_positive 등), ttl, created_at }
  2) 검증 시 challenge_id로 조회하여 판정.
  3) 성공 또는 시도 횟수 초과 시 삭제(또는 만료 대기).
- 장점: 서버 측 로직 변경 최소. 레이스/동시성 제어 용이. 파드 수와 무관하게 동작.
- 단점: 중앙 저장소 의존성, 네트워크 왕복 증가. Redis 권장(짧은 TTL, 높은 QPS에 적합). Mongo도 가능.
- 권장 스키마(예):
  - 컬렉션 `captcha_sessions`
    - _id: string (challenge_id)
    - type: string ("abstract" | "imagegrid" | "handwriting")
    - payload: object (타입별 필요한 정보)
    - attempts: int
    - ttl: int (초)
    - created_at: ISODate
- TTL 관리: Redis의 key TTL 또는 Mongo TTL 인덱스 사용.

타입별 최소 payload:
- Abstract: { target_class, keywords, image_keys(or URLs), positives_set(or flags) }
- Handwriting: { target_class, sample_keys(or URLs) }
- ImageGrid: { image_key(or URL) } (검증 시 ML 호출로 셀 계산)

### 대안 B. 완전 무상태(Stateless) 서명 토큰
- 개요: 서버가 생성 시 "검증에 필요한 모든 상태"를 클라이언트에게 내려주고, 이를 HMAC 서명으로 보호하여 클라이언트가 그대로 반송하게 합니다. 서버는 세션 저장 없이 토큰만 검증하여 판정.
- 공통 토큰 구조(예):
  ```json
  {
    "ver": 1,
    "typ": "abstract",
    "cid": "<challenge_id>",
    "exp": 1690000000,
    "payload": {}
  }
  ```
  - 토큰 직렬화(JSON) 후 base64url 인코딩 + HMAC-SHA256 서명(`ABSTRACT_HMAC_SECRET` 등 활용). JWT를 써도 무방하나, 커스텀 경량 포맷도 OK.
- 타입별 payload 예시:
  - Abstract: { target_class, keywords, images: [ {key, url} x9 ], positives: [2,3,7] }
    - 장점: 검증 시 토큰만으로 정답 세트를 복원 가능 → DB 조회 불필요.
    - 보안: positives는 서명으로 보호됨. exp 확인 필수.
  - Handwriting: { target_class, samples: [ {key,url} x≤5 ] }
  - ImageGrid: { image_key, url } (검증은 ML 호출 필요. 세션 대신 image_url을 토큰에서 읽음)
- 장점: 중앙 저장소 불필요, 파드 확장에 자연스러움.
- 단점: 토큰 크기 증가(특히 Abstract의 이미지 9장 메타데이터 포함), 모바일 환경에서 헤더/바디 크기 고려 필요. 키 롤오버 전략 필요.

보안 고려사항(Stateless 공통):
- exp(만료) 반드시 포함하고 유효성 검사.
- nonce/cid 포함으로 재사용 리스크 최소화.
- 서명 키 주기적 롤오버(키 ID 포함) 권장.
- 클라이언트가 변조할 수 있는 필드와 그렇지 않은 필드를 명확히 분리.

---

## 3. "AI 정답을 MongoDB에 미리 저장"을 활용한 구체 흐름

AI 추론 결과(예: 이미지 키별 target_class 적합도/라벨)를 미리 Mongo에 적재한다는 가정 하에 다음 두 경로가 가능합니다.

### 3.1 중앙 저장형(대안 A)
- 생성 시:
  - 9장의 이미지 키를 샘플링하여 challenge 문서에 저장.
  - positives_set은 Mongo 사전 지식으로 즉시 계산하여 함께 저장.
- 검증 시:
  - challenge_id 조회 → positives_set과 사용자의 selections 비교.
- 장점: 단순/안정적. 토큰 부담이 적음.

### 3.2 무상태형(대안 B)
- 생성 시:
  - 9장 이미지 키와 positives_set을 포함한 payload를 토큰화(HMAC 서명)하여 클라이언트로 전달.
- 검증 시:
  - 토큰 검증(서명/만료) 후 positives_set과 selections 비교.
- 장점: DB 조회 생략. 멀티 파드 완전 독립.

Handwriting 특이사항:
- 현재는 전역 `HANDWRITING_CURRENT_CLASS` 비교 구조 → 반드시 개선 필요.
- 권장:
  - 생성에서 challenge_id를 도입하고, samples와 target_class를 중앙 저장 또는 토큰에 포함.
  - 검증에서 전역 상태를 참조하지 않도록 수정.

ImageCaptcha 특이사항:
- 현재 검증에서 YOLO/ML을 호출하여 결과로 정답 셀을 계산 → 정답은 사전 저장 불필요.
- 필요한 것은 이미지 식별자(image_key/url)와 TTL뿐.
  - 중앙 저장형: 세션에 image_key 보관.
  - 무상태형: 토큰에 image_key/url 포함 후 검증 시 그대로 사용.

---

## 4. 마이그레이션 전략 및 최소 변경 가이드

- 점진적 롤아웃
  1) 신규 파라미터/포맷을 추가(예: verify 요청에 `token` 또는 `challenge_id` + `token` 허용).
  2) 구버전 클라이언트와의 호환 기간 동안 in-memory + 새로운 경로 병행 지원.
  3) 트래픽/에러율 모니터링 후 in-memory 경로 제거.

- 로깅/관측 포인트
  - 생성/검증에 대한 cid, typ, exp, 검증 결과, 시도 횟수, 토큰 검증 실패 사유(서명 불일치/만료 등).
  - 파드별 분포를 모니터링하여 sticky 없이도 성공률 유지 확인.

- 최소 변경으로 시작하려면
  - Redis를 도입해 세션 저장만 중앙화(대안 A) → 서버 코드 변경 폭이 가장 작음.
  - 동시에 Handwriting에 challenge_id 도입 및 전역 상태 의존 제거.

---

## 5. 권고 요약(Q1 최종 답변)
- 현 구조에서 in-memory에 세션/전역 정보를 기억하는 방식은 멀티 파드 무스티키 환경에서 근본적으로 취약하므로, 더 이상 유지하지 않는 것이 좋습니다.
- 이미 AI 정답을 MongoDB에 사전 적재하기로 했으므로, 다음 중 하나를 권장합니다.
  1) 중앙 저장형(권장: Redis): 모든 캡차 세션을 중앙 저장소에 TTL과 함께 저장/조회. 구현이 단순하고 안정적.
  2) 무상태 토큰형: 생성 시 정답/필수 정보를 서명된 토큰으로 내려주고, 검증 시 토큰만으로 판정. 저장소 불필요.
- 추가로 Handwriting 흐름은 반드시 challenge_id 또는 토큰 기반으로 개편하여 파드 전역 상태 의존을 제거해야 합니다.

---

## 6. 타입별 필요한 서버 상태(정리)
- Abstract:
  - 중앙 저장형: 9장 이미지 식별자와 정답 세트(혹은 플래그), target_class/keywords, TTL.
  - 무상태형: 위 정보를 토큰 payload에 포함하고 서명.
- Handwriting:
  - 중앙 저장형: target_class, 샘플 이미지 식별자, TTL.
  - 무상태형: target_class와 샘플 목록을 토큰화.
- ImageCaptcha(3x3):
  - 중앙 저장형: image_key/url, TTL, (시도 횟수).
  - 무상태형: image_key/url, TTL을 토큰에 포함.

---

## 7. 부록: 변경 체크리스트(요약)
- [ ] Handwriting: challenge_id 신설, verify에서 전역 상태 제거.
- [ ] Abstract: verify 시 세션 저장소에서 조회하거나 토큰으로 복원할 수 있도록 변경.
- [ ] ImageCaptcha: image_url을 중앙 저장/토큰에서 가져오도록 변경.
- [ ] TTL 및 시도 횟수 관리(중앙 저장형 시 원자적 증가/만료 필요).
- [ ] HMAC 서명 키 관리, 만료 검사, 키 롤오버 체계.
- [ ] 기존 signatures 필드의 역할 재정의 또는 폐기(무결성 vs 판정 복원).

以上.
