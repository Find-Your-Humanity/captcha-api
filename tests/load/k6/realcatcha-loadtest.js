import http from 'k6/http';
import { sleep, check } from 'k6';
import {Trend, Counter, Rate, Gauge} from 'k6/metrics';

/*****
Realcatcha k6 부하 테스트 (혼합 또는 단일 시나리오)
환경 변수:
  - BASE_URL=https://api.realcatcha.com
  - API_KEY=rc_live_xxx (필수)
  - SECRET_KEY=rc_sk_xxx (데모 키 사용 시 선택)
  - SCENARIO=mix|next|image|abstract|handwriting (기본값: mix)
  - DURATION=5m (기본값)
  - AVG_SOLVE_SECONDS=5 (사용자가 캡차 하나를 푸는 평균 소요 시간, 초 단위)
  - LOAD_STRATEGY=users|rate (기본값: users; users는 동시 사용자 최대화, rate는 RPS 고정)
  - TARGET_USERS=100 (선택; users 모드에서는 VUs로 사용, rate 모드에서는 RATE ≈ TARGET_USERS/AVG_SOLVE_SECONDS 계산)
  - RATE=50 (초당 반복 수; rate 모드에서 사용, 설정 시 TARGET_USERS보다 우선 적용)
  - HEADERS_ONLY=true|false (기본 false; true면 응답 바디 파싱을 건너뜀)
설명:
  - 본 스크립트는 정답/토큰의 정확성에 의존하지 않도록, 대규모 환경에서 검증이 필요 없는 "문제 생성" 엔드포인트 중심으로 트래픽을 유도합니다.
  - users 모드에서는 각 VU(사용자)가 요청을 1회 수행한 뒤 AVG_SOLVE_SECONDS 만큼 대기하여 per-user RPS를 극도로 낮추고, 전체 동시 사용자 수를 최대화하는데 초점을 둡니다.
  - 검증(verify) 엔드포인트는 별도의 저용량 스모크 테스트 스크립트로 분리하여 운영하는 것을 권장합니다.
*****/

const BASE_URL = __ENV.BASE_URL || 'https://api.realcatcha.com';
const API_KEY = __ENV.API_KEY || 'rc_live_f49a055d62283fd02e8203ccaba70fc2';
const SECRET_KEY = __ENV.SECRET_KEY || '';
const SCENARIO = (__ENV.SCENARIO || 'mix').toLowerCase();
const DURATION = __ENV.DURATION || '5m';
const AVG_SOLVE_SECONDS = Number(__ENV.AVG_SOLVE_SECONDS || 5);
const TARGET_USERS = __ENV.TARGET_USERS ? Number(__ENV.TARGET_USERS) : 10000;
const RATE = __ENV.RATE ? Number(__ENV.RATE) : (TARGET_USERS ? Math.max(1, Math.ceil(TARGET_USERS / AVG_SOLVE_SECONDS)) : 80); // 초당 반복(iterations per second)
const HEADERS_ONLY = String(__ENV.HEADERS_ONLY || 'false').toLowerCase() === 'true';
const LOAD_STRATEGY = (__ENV.LOAD_STRATEGY || 'users').toLowerCase(); // users: 동시 사용자 최대화, rate: RPS 고정

// 사용자 지정 메트릭(지표) 정의: 각 엔드포인트의 응답 시간 트렌드와 요청 수, 오류율 등을 수집하여 성능을 모니터링
const imageTrend = new Trend('image_response_time');
const abstractTrend = new Trend('abstract_response_time');
const handwritingTrend = new Trend('handwriting_response_time');
const nextTrend = new Trend('next_response_time');

const requestCount = new Counter('total_requests');
const scenarioCount = {
  image: new Counter('image_requests'),
  abstract: new Counter('abstract_requests'),
  handwriting: new Counter('handwriting_requests'),
  next: new Counter('next_requests'),
};

const errorRate = new Rate('error_rate');

// 동시성 산정 로직:
// - TARGET_USERS가 지정된 경우: 해당 값을 그대로 동시 사용자 수(Desired Concurrency)로 사용
// - 미지정 시: RATE(초당 반복 수) * AVG_SOLVE_SECONDS(평균 풀이시간)를 통해 동시에 진행될 작업 수를 근사치로 계산
const DESIRED_CONCURRENCY = TARGET_USERS ? TARGET_USERS : Math.ceil(RATE * AVG_SOLVE_SECONDS);
const PRE_ALLOCATED_VUS = Math.min(Math.max(DESIRED_CONCURRENCY, 20), 10000); // 최소 20, 최대 10000 범위에서 사전 할당할 VU 수(초기 동시성 확보)
const MAX_VUS = Math.min(Math.max(Math.ceil(PRE_ALLOCATED_VUS * 2), 50), 20000); // 런타임 상한: 필요 시 사전 할당의 약 2배까지 확장(최소 50, 최대 20000)

// 보고 및 분석을 위한 게이지 메트릭: 목표 동시 사용자 수와 실제 유효 RATE를 각 반복마다 기록
const targetUsersGauge = new Gauge('target_users');
const rateGauge = new Gauge('effective_rate');

// 유효 RATE 계산: users 모드에서는 동시 사용자 수를 평균 풀이 시간으로 나눈 근사 RPS, rate 모드에서는 지정된 RATE 사용
const EFFECTIVE_RATE = (LOAD_STRATEGY === 'rate') ? RATE : Math.max(1, Math.ceil(DESIRED_CONCURRENCY / AVG_SOLVE_SECONDS));

if (!API_KEY) {
  throw new Error('API_KEY is required. Set env API_KEY=...');
}

// k6의 setup(): 테스트 시작 전에 한 번만 실행되며, 설정값(시나리오/지속시간/동시성 등)을 콘솔에 출력하여 실행 구성을 명확히 합니다.
export function setup() {
  console.log('Test Configuration:');
  console.log(`Base URL: ${BASE_URL}`);
  console.log(`Scenario: ${SCENARIO}`);
  console.log(`Duration: ${DURATION}`);
  console.log(`Avg Solve Seconds: ${AVG_SOLVE_SECONDS}`);
  console.log(`Target Users: ${TARGET_USERS !== null ? TARGET_USERS : 'n/a'}`);
  console.log(`Load Strategy: ${LOAD_STRATEGY}`);
  console.log(`Effective Rate: ${EFFECTIVE_RATE} requests/second`);
  console.log(`PreAllocated VUs: ${PRE_ALLOCATED_VUS}`);
  console.log(`Max VUs: ${MAX_VUS}`);
  console.log(`Headers Only: ${HEADERS_ONLY}`);
}

// k6 실행 옵션:
// - scenarios.main.executor: constant-arrival-rate (초당 정확한 요청 수 유지)
// - rate/timeUnit/duration: 요청 발생률과 총 테스트 시간을 정의
// - preAllocatedVUs/maxVUs: 미리 할당할 VU 수와 런타임 상한을 지정하여 부하 스파이크에 대응
// - thresholds: 실패율 및 지연 시간 SLO를 선언적으로 기술하여 실패 시 테스트를 실패 처리
const MAIN_SCENARIO = (LOAD_STRATEGY === 'users')
  ? {
      executor: 'constant-vus',
      vus: PRE_ALLOCATED_VUS,
      duration: DURATION,
      exec: 'runner',
    }
  : {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: PRE_ALLOCATED_VUS,
      maxVUs: MAX_VUS,
      exec: 'runner',
    };

export const options = {
  scenarios: {
    main: MAIN_SCENARIO,
  },
  thresholds: {
    http_req_failed: ['rate<0.02'], // 실패율 < 2% 유지
    http_req_duration: [
      'p(95)<800', // 95% 요청 지연이 800ms 미만 (문제 생성 엔드포인트 기준)
      'p(99)<2000', // 99% 요청 지연이 2000ms 미만
    ],
  },
};

// 요청에 사용할 공통 헤더를 구성합니다.
// - includeSecret=true인 경우, SECRET_KEY가 설정되어 있으면 x-secret-key 헤더를 추가합니다.
function headers(includeSecret = true) {
  const h = {
    'Content-Type': 'application/json',
    'x-api-key': API_KEY,
    'User-Agent': 'k6-loadtest/realcatcha',
  };
  if (SECRET_KEY && includeSecret) h['x-secret-key'] = SECRET_KEY;
  return h;
}

// 최소한의 사용자 행동 데이터(마우스 이동/클릭/스크롤/페이지 체류)를 생성합니다.
// - 일부 엔드포인트는 봇 감지 우회를 위해 행동 데이터가 필요할 수 있으므로, 간단한 샘플을 제공합니다.
function smallBehaviorData() {
  const now = Date.now();
  return {
    mouseMovements: [
      { x: 10, y: 20, t: now - 300 },
      { x: 12, y: 22, t: now - 200 },
      { x: 17, y: 24, t: now - 100 },
    ],
    mouseClicks: [ { x: 15, y: 23, t: now - 50 } ],
    scrollEvents: [ { dY: 120, t: now - 150 } ],
    pageEvents: { enterTime: now - 5000, exitTime: now, totalTime: 5000 },
  };
}

// Next-Captcha 엔드포인트에 문제 생성을 요청합니다.
// - 행동 데이터(payload)를 함께 전송하며, 응답 시간 측정 및 상태 코드 검증, 메트릭 기록을 수행합니다.
function postNextCaptcha() {
  const url = `${BASE_URL}/api/next-captcha`;
  const payload = JSON.stringify({ behavior_data: smallBehaviorData() });
  const start = new Date();
  const res = http.post(url, payload, {headers: headers(true)});
  const duration = new Date() - start;

  requestCount.add(1);
  scenarioCount.next.add(1);
  nextTrend.add(duration);

  const success = check(res, {
    'next-captcha status is 2xx/3xx': (r) => r.status >= 200 && r.status < 400,
  });
  errorRate.add(!success);

  if (!HEADERS_ONLY) {
    try { res.json(); } catch (e) {}
  }
}

// 이미지 캡차 문제 생성 엔드포인트 호출.
// - 인증 헤더 포함 요청을 전송하고, 응답 시간 추적 및 상태 코드 확인, 관련 메트릭을 누적합니다.
function postImageChallenge() {
  const url = `${BASE_URL}/api/image-challenge`;
  const start = new Date();
  const res = http.post(url, null, {headers: headers(true)});
  const duration = new Date() - start;

  requestCount.add(1);
  scenarioCount.image.add(1);
  imageTrend.add(duration);

  const success = check(res, {
    'image-challenge status is 2xx/3xx': (r) => r.status >= 200 && r.status < 400,
  });
  errorRate.add(!success);
}

// 추상(abstract) 캡차 문제 생성 엔드포인트 호출.
// - 인증 없이 접근 가능한 공개 엔드포인트로, 응답 시간/상태 코드/메트릭을 기록합니다.
function postAbstractCaptcha() {
  const url = `${BASE_URL}/api/abstract-challenge`;
  const start = new Date();
  const res = http.post(url, null, {headers: headers(false)}); // 인증 불필요 (공개 엔드포인트)
  const duration = new Date() - start;

  requestCount.add(1);
  scenarioCount.abstract.add(1);
  abstractTrend.add(duration);

  const success = check(res, {
    'abstract-captcha status is 2xx/3xx': (r) => r.status >= 200 && r.status < 400,
  });
  errorRate.add(!success);
}

// 손글씨(handwriting) 캡차 문제 생성 엔드포인트 호출.
// - 공개 엔드포인트이며, 응답 시간 측정과 상태 코드 확인, 관련 메트릭 기록을 수행합니다.
function postHandwritingChallenge() {
  const url = `${BASE_URL}/api/handwriting-challenge`;
  const start = new Date();
  const res = http.post(url, null, {headers: headers(false)});
  const duration = new Date() - start;

  requestCount.add(1);
  scenarioCount.handwriting.add(1);
  handwritingTrend.add(duration);

  const success = check(res, {
    'handwriting-challenge status is 2xx/3xx': (r) => r.status >= 200 && r.status < 400,
  });
  errorRate.add(!success);
}

// 혼합(mix) 시나리오 실행 로직.
// - 요청을 무작위로 분배하여 이미지/추상/손글씨 엔드포인트를 비율대로 호출합니다.
function runMix() {
  // 트래픽 분배: 이미지 60%, 추상 20%, 손글씨 20%
  const r = Math.random();
  if (r < 0.6) return postImageChallenge();
  if (r < 0.8) return postAbstractCaptcha();
  return postHandwritingChallenge();
}

// k6 시나리오의 각 반복에서 실행되는 메인 함수.
// - 시나리오 선택에 따라 적절한 엔드포인트를 호출하고, 관측용 게이지를 기록한 뒤 짧은 think-time을 둡니다.
export function runner() {
  // 가시성 확보를 위해 각 반복(iteration)마다 구성 관련 게이지 메트릭을 기록
  targetUsersGauge.add(DESIRED_CONCURRENCY);
  rateGauge.add(EFFECTIVE_RATE);
  switch (SCENARIO) {
    case 'next':
      console.log('next scenario is disabled');
      break;
    case 'image':
      postImageChallenge();
      break;
    case 'abstract':
      postAbstractCaptcha();
      break;
    case 'handwriting':
      postHandwritingChallenge();
      break;
    case 'mix':
    default:
      runMix();
  }
  sleep(LOAD_STRATEGY === 'users' ? AVG_SOLVE_SECONDS : 0.1); // users 모드에서는 평균 풀이시간만큼 대기하여 RPS 최소화
}
