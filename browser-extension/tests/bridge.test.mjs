import test from "node:test";
import assert from "node:assert/strict";
import {createBridgeClient} from "../bridge.mjs";

const jobId = "1234567890abcdef1234567890abcdef";
const token = "local-pairing-token";
const request = {operation: "approve_cleanup", request_id: "keep-this-id", target: {id: "file-1", revision: "review-2"}};
const pending = {status: 202, body: {ok: true, job_id: jobId}};
const completed = {status: 200, body: {ok: true, state: {generation: "openrouter", cleanup: {items: []}}}};

function harness(responses, {wait} = {}) {
  const calls = [], delays = [];
  let clock = 0;
  const client = createBridgeClient({
    now: () => clock,
    sleep: async (milliseconds) => {delays.push(milliseconds); clock += wait ?? milliseconds;},
    fetchImpl: async (url, options) => {
      calls.push({url, options});
      const next = responses.shift();
      assert(next, "Unexpected extra request");
      if (next instanceof Error) throw next;
      return {status: next.status, ok: next.status >= 200 && next.status < 300, json: async () => next.body};
    },
  });
  return {client, calls, delays};
}

test("synchronous dispatch preserves the reviewed action and returns without polling", async () => {
  const {client, calls, delays} = harness([completed]);
  assert.deepEqual(await client.dispatch(token, request), completed.body);
  assert.equal(calls.length, 1);
  assert.deepEqual(JSON.parse(calls[0].options.body), request);
  assert.deepEqual(delays, []);
});

test("202 dispatch polls the same job with authentication until final state", async () => {
  const {client, calls, delays} = harness([pending, pending, completed]);
  assert.deepEqual(await client.dispatch(token, request), completed.body);
  assert.deepEqual(delays, [1000, 1000]);
  assert.deepEqual(calls.map(({url}) => new URL(url).pathname), ["/v1/dispatch", `/v1/jobs/${jobId}`, `/v1/jobs/${jobId}`]);
  assert.deepEqual(calls.map(({options}) => options.method), ["POST", "GET", "GET"]);
  assert.deepEqual(JSON.parse(calls[0].options.body), request);
  for (const {options} of calls) {
    assert.equal(options.headers.Authorization, `Bearer ${token}`);
    assert.equal(options.credentials, "omit");
    assert.equal(options.redirect, "error");
    assert.equal(options.cache, "no-store");
    assert(options.signal instanceof AbortSignal);
  }
});

test("a failed async job returns its original error without repeating approval", async () => {
  const failed = {ok: false, error: {code: "stale_review", message: "Request a fresh review."}};
  const {client, calls} = harness([pending, {status: 409, body: failed}]);
  assert.deepEqual(await client.dispatch(token, request), failed);
  assert.equal(calls.filter(({options}) => options.method === "POST").length, 1);
});

test("lost polling response reports an uncertain outcome and never retries the action", async () => {
  const {client, calls} = harness([pending, new Error("connection lost")]);
  const result = await client.dispatch(token, request);
  assert.equal(result.error.code, "bridge_unavailable");
  assert.match(result.error.message, /refresh.*before retrying/i);
  assert.equal(calls.length, 2);
});

test("pending jobs stop after 240 seconds with instructions to check state", async () => {
  const {client, calls} = harness([pending], {wait: 240000});
  const result = await client.dispatch(token, request);
  assert.equal(result.error.code, "bridge_timeout");
  assert.match(result.error.message, /may finish.*Refresh/);
  assert.equal(calls.length, 1);
});

test("time spent in the worker queue reduces polling time and expired requests are not submitted", async () => {
  const expired = harness([]);
  assert.equal((await expired.client.dispatch(token, request, 0)).error.code, "bridge_timeout");
  assert.equal(expired.calls.length, 0);
  const queued = harness([pending]);
  assert.equal((await queued.client.dispatch(token, request, 500)).error.code, "bridge_timeout");
  assert.deepEqual(queued.delays, [500]);
  assert.equal(queued.calls.length, 1);
});

test("invalid or changed job identifiers never become another operation", async () => {
  const malformed = harness([{status: 202, body: {ok: true, job_id: "../dispatch"}}]);
  assert.equal((await malformed.client.dispatch(token, request)).error.code, "bridge_response");
  assert.equal(malformed.calls.length, 1);
  const changed = harness([pending, {status: 202, body: {ok: true, job_id: "a".repeat(32)}}]);
  assert.equal((await changed.client.dispatch(token, request)).error.code, "bridge_response");
  assert.equal(changed.calls.length, 2);
});

test("a final successful response must contain task state", async () => {
  const {client} = harness([pending, {status: 200, body: {ok: true, job_id: jobId}}]);
  assert.equal((await client.dispatch(token, request)).error.code, "bridge_response");
});
