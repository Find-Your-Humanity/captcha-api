import http from 'k6/http';
import { sleep, check } from 'k6';
import {Trend, Counter, Rate} from 'k6/metrics';

/*****
Realcatcha k6 Load Test (mixed or single-scenario)
Env vars:
  - BASE_URL=https://api.realcatcha.com
  - API_KEY=rc_live_xxx (required)
  - SECRET_KEY=rc_sk_xxx (optional if demo key)
  - SCENARIO=mix|next|image|abstract|handwriting (default: mix)
  - DURATION=10m (default)
  - RATE=50 (iterations per second; used in constant-arrival-rate exec)
  - HEADERS_ONLY=true|false (default false; if true, skip response body parsing)
Notes:
  - This script focuses on challenge creation endpoints to avoid reliance on correct answers/tokens at scale.
  - Verify endpoints can be added in a separate, low-volume smoke script if needed.
*****/

const BASE_URL = __ENV.BASE_URL || 'https://api.realcatcha.com';
const API_KEY = __ENV.API_KEY || 'rc_live_f49a055d62283fd02e8203ccaba70fc2';
const SECRET_KEY = __ENV.SECRET_KEY || '';
const SCENARIO = (__ENV.SCENARIO || 'mix').toLowerCase();
const DURATION = __ENV.DURATION || '5m';
const RATE = Number(__ENV.RATE || 80); // iters/s
const HEADERS_ONLY = String(__ENV.HEADERS_ONLY || 'false').toLowerCase() === 'true';

// Custom metrics
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

if (!API_KEY) {
  throw new Error('API_KEY is required. Set env API_KEY=...');
}

export function setup() {
  console.log('Test Configuration:');
  console.log(`Base URL: ${BASE_URL}`);
  console.log(`Scenario: ${SCENARIO}`);
  console.log(`Duration: ${DURATION}`);
  console.log(`Rate: ${RATE} requests/second`);
  console.log(`Headers Only: ${HEADERS_ONLY}`);
}

export const options = {
  scenarios: {
    main: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: Math.min(Math.max(RATE * 2, 20), 1000),
      maxVUs: Math.min(Math.max(RATE * 4, 50), 2000),
      exec: 'runner',
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.02'], // <2% failures
    http_req_duration: [
      'p(95)<800', // 95% under 800ms (challenge endpoints)
      'p(99)<2000',
    ],
  },
};

function headers(includeSecret = true) {
  const h = {
    'Content-Type': 'application/json',
    'x-api-key': API_KEY,
    'User-Agent': 'k6-loadtest/realcatcha',
  };
  if (SECRET_KEY && includeSecret) h['x-secret-key'] = SECRET_KEY;
  return h;
}

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

function postAbstractCaptcha() {
  const url = `${BASE_URL}/api/abstract-captcha`;
  const start = new Date();
  const res = http.post(url, null, {headers: headers(false)}); // no auth needed
  const duration = new Date() - start;

  requestCount.add(1);
  scenarioCount.abstract.add(1);
  abstractTrend.add(duration);

  const success = check(res, {
    'abstract-captcha status is 2xx/3xx': (r) => r.status >= 200 && r.status < 400,
  });
  errorRate.add(!success);
}

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

function runMix() {
  // 60% image, 20% abstract, 20% handwriting
  const r = Math.random();
  if (r < 0.6) return postImageChallenge();
  if (r < 0.8) return postAbstractCaptcha();
  return postHandwritingChallenge();
}

export function runner() {
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
  sleep(0.1); // tiny think-time to avoid pure hammering
}
