# Realcatcha Captcha API Load Test (k6)

이 디렉토리는 api.realcatcha.com 캡차 발급 API 중심의 트래픽 테스트를 위한 k6 스크립트를 제공합니다.

주의
- 운영 트래픽에 영향을 줄 수 있으므로, 반드시 관계자 승인 후 제한된 시간/율에서 실행하세요.
- ML/OCR/스토리지 등의 외부 연동에 과부하를 주지 않도록 기본 스크립트는 발급(creation) 엔드포인트에 초점을 맞춥니다.
- 데모 키는 공개키만으로 호출 가능하지만, 실제 집계/로그 적재는 일반 키 사용 시에 수행됩니다.

파일
- realcatcha-loadtest.js: 단일/혼합 시나리오 실행 스크립트

사전 준비(환경 변수)
- BASE_URL: 대상 베이스 URL (기본값: https://api.realcatcha.com)
- API_KEY: 발급받은 공개 키(필수)
- SECRET_KEY: 발급받은 비밀 키(데모 키 사용 시 생략 가능)
- SCENARIO: mix | next | image | abstract | handwriting (기본: mix)
- DURATION: 테스트 시간 (기본: 10m)
- RATE: 초당 반복 수(Iterations per second, 기본: 50)
- HEADERS_ONLY: true/false (기본: false, true면 응답 바디 파싱 생략)

실행 예시
- 로컬
  - k6 run backend/captcha-api/tests/load/k6/realcatcha-loadtest.js
  - 예) BASE_URL=https://api.realcatcha.com API_KEY=rc_live_xxx SECRET_KEY=rc_sk_xxx k6 run backend/captcha-api/tests/load/k6/realcatcha-loadtest.js

- Docker
  - docker run --rm -e BASE_URL -e API_KEY -e SECRET_KEY -e SCENARIO -e DURATION -e RATE -v "%cd%":/scripts grafana/k6 run /scripts/backend/captcha-api/tests/load/k6/realcatcha-loadtest.js

- Kubernetes(Job) 힌트
  - grafana/k6 이미지를 사용하는 Job을 작성하여 동일한 환경변수로 실행
  - 실행 중/후 메트릭은 Prometheus/Grafana, 로그는 Loki에서 관찰

권장 임계치(Thresholds)
- http_req_failed < 2%
- http_req_duration p(95) < 800ms, p(99) < 2000ms (발급 API 기준)

시나리오 설명
- next: /api/next-captcha (행동 데이터 기반 다음 캡차 결정)
- image: /api/image-challenge (그리드형 발급)
- abstract: /api/abstract-challenge (추상 이미지 발급)
- handwriting: /api/handwriting-challenge (손글씨 발급)
- mix: 50% next, 30% image, 10% abstract, 10% handwriting

추가 팁
- Ingress의 cookie affinity가 부하 분산에 영향을 줄 수 있으므로 테스트 구간에 한시적으로 조정 검토
- 장시간 테스트(Soak)는 Redis TTL, DB 로그 테이블 크기 증가에 유의
- ML/OCR 외부 호출이 과도한 경우 Feature flag/환경변수로 비활성화하거나 스텁으로 대체
