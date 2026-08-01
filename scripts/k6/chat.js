// k6 load test for the builder /chat endpoint and the commerce read paths.
//
// COST SAFETY. Every request carries X-Palladium-Load-Test, which routes the
// builder into stub LMs — see backend/app/core/loadtest.py. Without a matching
// LOAD_TEST_SECRET on the server the header is ignored and the run makes REAL
// OpenRouter calls, one per build step per virtual user. The setup() check
// below aborts the run rather than let that happen silently.
//
// Run (see deploy/observability.md for the credentials):
//   K6_PROMETHEUS_RW_SERVER_URL=... \
//   K6_PROMETHEUS_RW_USERNAME=... K6_PROMETHEUS_RW_PASSWORD=... \
//   BASE_URL=https://api.palladiumtech.ai LOAD_TEST_SECRET=... \
//   k6 run -o experimental-prometheus-rw scripts/k6/chat.js

import http from 'k6/http';
import { check, fail, group, sleep } from 'k6';
import { Trend } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const COMMERCE_URL = __ENV.COMMERCE_URL || BASE_URL;
const SECRET = __ENV.LOAD_TEST_SECRET || '';

// Time-to-first-byte is the number that matters for /chat: the endpoint streams
// SSE, so total duration mostly measures how long the stream stayed open and
// says little about whether the service is healthy. This mirrors the
// should_exclude_streaming_duration choice on the server side.
const chatTTFB = new Trend('chat_time_to_first_byte', true);

export const options = {
  scenarios: {
    // Reads are cheap and concurrent; keep them steady as background load.
    browse: {
      executor: 'constant-vus',
      vus: 10,
      duration: '2m',
      exec: 'browse',
    },
    // /chat is the expensive path. Ramp rather than slam: builder's HPA needs
    // 60-120s to schedule a pod on Autopilot, so an instant spike measures
    // cold-start, not steady-state capacity.
    chat: {
      executor: 'ramping-vus',
      startVUs: 1,
      stages: [
        { duration: '1m', target: 5 },
        { duration: '2m', target: 15 },
        { duration: '1m', target: 0 },
      ],
      exec: 'chat',
      gracefulRampDown: '30s',
    },
  },
  thresholds: {
    // Deliberately loose on /chat: the stub adds an artificial 400ms TTFT
    // (LOAD_TEST_STUB_TTFT_MS), so anything under ~1s is service overhead.
    chat_time_to_first_byte: ['p(95)<1500'],
    'http_req_duration{scenario:browse}': ['p(95)<500'],
    http_req_failed: ['rate<0.01'],
  },
};

function headers() {
  return {
    'Content-Type': 'application/json',
    'X-Palladium-Load-Test': SECRET,
  };
}

// Fail fast and loudly rather than quietly spending real tokens.
export function setup() {
  if (!SECRET) {
    fail(
      'LOAD_TEST_SECRET is not set. Refusing to run: without it the server ' +
        'ignores the load-test header and every /chat request bills real ' +
        'OpenRouter tokens.'
    );
  }

  const res = http.post(
    `${BASE_URL}/api/v1/chat`,
    JSON.stringify({ messages: [{ role: 'user', content: 'hello' }] }),
    { headers: headers(), timeout: '60s' }
  );

  if (res.status !== 200) {
    fail(`preflight /chat returned ${res.status}; aborting before load starts`);
  }
  // The stub says so in its own text. If this marker is missing the secret was
  // rejected and the response came from a real model — stop now.
  if (!String(res.body).includes('load-test mode')) {
    fail(
      'preflight response was NOT served by the stub LM — the secret is wrong ' +
        'or LOAD_TEST_SECRET is unset on the server. Aborting to avoid ' +
        'spending real tokens.'
    );
  }
}

export function chat() {
  group('chat', () => {
    const start = Date.now();
    const res = http.post(
      `${BASE_URL}/api/v1/chat`,
      JSON.stringify({
        messages: [
          { role: 'user', content: 'I want a gaming PC for about $1500' },
        ],
      }),
      { headers: headers(), timeout: '120s' }
    );

    // k6 buffers the whole SSE response, so this is really time-to-last-byte
    // for short stub replies. Good enough to catch regressions; swap for
    // k6/experimental/streams if per-event timing ever matters.
    chatTTFB.add(Date.now() - start);

    check(res, {
      'chat 200': (r) => r.status === 200,
      'chat served by stub': (r) => String(r.body).includes('load-test mode'),
    });
  });

  sleep(Math.random() * 3 + 2);
}

export function browse() {
  group('listings', () => {
    const list = http.get(`${COMMERCE_URL}/api/v1/listings/`, {
      headers: headers(),
    });
    check(list, { 'listings 200': (r) => r.status === 200 });
  });

  sleep(1);
}
