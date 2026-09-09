/**
 * Server calls.
 *
 * The session cookie is HttpOnly, so JS never reads it — fetch sends it automatically.
 * `credentials: "same-origin"` states that explicitly: we are on one origin, and the
 * session context the agent inherits travels on this cookie.
 */

async function request(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers ?? {}) },
    ...options,
  });

  let payload = null;
  try {
    payload = await response.json();
  } catch {
    // Some error responses have no body
  }

  if (!response.ok) {
    const detail = payload?.detail ?? `${response.status} ${response.statusText}`;
    const error = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    error.status = response.status;
    error.payload = payload;
    throw error;
  }

  return payload;
}

export function readSession() {
  return request("/api/session");
}

export function findLine({ phrase, tone = "", limit = 20 }) {
  return request("/api/find_line", {
    method: "POST",
    body: JSON.stringify({ phrase, tone, limit }),
  });
}

/**
 * The words of specific lines, with their timings.
 *
 * `find_line` returns the matched range only; this returns the sentence around it, which
 * is what makes the transcript clickable at word level. Batched into one request because
 * a search can return fifty candidates and fifty round trips to answer one question is
 * the wrong shape.
 */
export function readLines(refs) {
  return request("/api/lines", {
    method: "POST",
    body: JSON.stringify({
      lines: refs.map(({ take_id, line_id }) => ({ take_id, line_id })),
    }),
  });
}

export function wordOccurrences(word, tone = "") {
  const query = tone ? `?tone=${encodeURIComponent(tone)}` : "";
  return request(`/api/word/${encodeURIComponent(word)}${query}`);
}

export function libraryStats() {
  return request("/api/library/stats");
}

export function chatStatus() {
  return request("/api/chat/status");
}

export function sendChat(message) {
  return request("/api/chat", {
    method: "POST",
    body: JSON.stringify({ message }),
  });
}

export function requestRender(segments) {
  return request("/api/render", {
    method: "POST",
    body: JSON.stringify({
      segments: segments.map((segment) => ({
        candidate_id: segment.id,
        start_ms: segment.start_ms,
        end_ms: segment.end_ms,
      })),
    }),
  });
}

export function renderStatus(jobId) {
  return request(`/api/render/${encodeURIComponent(jobId)}`);
}

/** Polls until the job settles. Rendering is CPU work with an unpredictable duration. */
export async function waitForRender(jobId, { intervalMs = 700, timeoutMs = 120000 } = {}) {
  const deadline = Date.now() + timeoutMs;
  let job = await renderStatus(jobId);
  while (job.status === "queued" || job.status === "running") {
    if (Date.now() > deadline) {
      throw new Error(`The render timed out (${job.status})`);
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
    job = await renderStatus(jobId);
  }
  return job;
}
