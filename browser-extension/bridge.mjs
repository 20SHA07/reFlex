const BRIDGE = "http://127.0.0.1:8765";
const REQUEST_TIMEOUT_MS = 15000;
export const BRIDGE_JOB_TIMEOUT_MS = 240000;
const POLL_INTERVAL_MS = 1000;
const error = (code, message) => ({ok: false, error: {code, message}});

// Each fetch finishes within the service worker's fetch limit. A dispatch is
// submitted only once; polling can never repeat an approval or file operation.
export function createBridgeClient({
  fetchImpl = (...args) => fetch(...args),
  now = () => Date.now(),
  sleep = (milliseconds) => new Promise(resolve => setTimeout(resolve, milliseconds)),
} = {}) {
  async function request(path, token, body, timeout = REQUEST_TIMEOUT_MS) {
    try {
      const response = await fetchImpl(`${BRIDGE}${path}`, {
        method: body ? "POST" : "GET", credentials: "omit", redirect: "error", cache: "no-store",
        headers: {Authorization: `Bearer ${token}`, ...(body ? {"Content-Type": "application/json"} : {})},
        ...(body ? {body: JSON.stringify(body)} : {}), signal: AbortSignal.timeout(timeout),
      });
      const result = await response.json();
      if (typeof result?.ok !== "boolean" || (!response.ok && result.ok)) {
        return {status: response.status, result: error("bridge_response", "The local referee returned an invalid response.")};
      }
      return {status: response.status, result};
    } catch {
      return {status: 0, result: error("bridge_unavailable", "Cannot confirm the local referee's response. Check browser_bridge.py and pairing, then refresh to see whether the action completed before retrying.")};
    }
  }

  return {
    async session(token) {
      const {status, result} = await request("/v1/session", token);
      return status === 202 ? error("bridge_response", "The local referee returned an invalid session response.") : result;
    },
    async dispatch(token, body, queuedDeadline = now() + BRIDGE_JOB_TIMEOUT_MS) {
      const deadline = Math.min(queuedDeadline, now() + BRIDGE_JOB_TIMEOUT_MS);
      const timeout = () => error("bridge_timeout", "The AI task is still taking time. It may finish in the local bridge. Refresh to check its state before starting it again.");
      const initialRemaining = deadline - now();
      if (initialRemaining <= 0) return timeout();
      let response = await request("/v1/dispatch", token, body, Math.min(REQUEST_TIMEOUT_MS, initialRemaining));
      let jobId;
      while (response.status === 202 && response.result.ok) {
        const receivedId = response.result.job_id;
        if (typeof receivedId !== "string" || !/^[a-f0-9]{32}$/.test(receivedId) || (jobId && jobId !== receivedId)) {
          return error("bridge_response", "The local referee returned an invalid task response. Refresh before retrying.");
        }
        jobId = receivedId;
        const remaining = deadline - now();
        if (remaining <= 0) return timeout();
        await sleep(Math.min(POLL_INTERVAL_MS, remaining));
        const pollingRemaining = deadline - now();
        if (pollingRemaining <= 0) return timeout();
        response = await request(`/v1/jobs/${jobId}`, token, undefined, Math.min(REQUEST_TIMEOUT_MS, pollingRemaining));
      }
      if (now() >= deadline) return timeout();
      if (response.result.ok && (!response.result.state || typeof response.result.state !== "object")) {
        return error("bridge_response", "The local referee returned no task state. Refresh before retrying.");
      }
      return response.result;
    },
  };
}
