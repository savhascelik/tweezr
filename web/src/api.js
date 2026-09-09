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

/**
 * Every word in the library, and the takes it is spread across.
 *
 * Behind the vocabulary panel. Without it the page is a memory test: you have to type a
 * phrase you already know, which does not survive contact with footage you just brought
 * in — the transcriber does not always hear what you said.
 *
 * `take` narrows it to one recording, `tone` follows the delivery filter so a chip cannot
 * promise five occurrences and then return nothing when clicked.
 */
export function vocabulary({ take = "", tone = "" } = {}) {
  const query = new URLSearchParams();
  if (take) query.set("take", take);
  if (tone) query.set("tone", tone);
  const suffix = query.toString();
  return request(`/api/vocabulary${suffix ? `?${suffix}` : ""}`);
}

export function chatStatus() {
  return request("/api/chat/status");
}

export function sendChat(message, context = null) {
  return request("/api/chat", {
    method: "POST",
    body: JSON.stringify({ message, context }),
  });
}

export function uploadStatus() {
  return request("/api/upload/status");
}

export function uploadJob(jobId) {
  return request(`/api/upload/${encodeURIComponent(jobId)}`);
}

/**
 * Sends a recording and reports progress while it goes.
 *
 * XMLHttpRequest rather than fetch, for one reason: fetch has no upload progress event.
 * A hundred megabytes over a slow connection with no indicator reads as a broken page,
 * and the whole point of this control is that a visitor can bring their own footage.
 */
export function uploadMedia(file, { label = "", language = "", onProgress } = {}) {
  const form = new FormData();
  form.append("file", file);
  form.append("label", label);
  form.append("language", language);

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/upload");
    xhr.withCredentials = true;

    xhr.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) onProgress?.(event.loaded / event.total);
    });

    xhr.addEventListener("load", () => {
      let payload = null;
      try {
        payload = JSON.parse(xhr.responseText);
      } catch {
        // An error response may not carry a body
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(payload);
        return;
      }
      const detail = payload?.detail ?? `${xhr.status} ${xhr.statusText}`;
      const error = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      error.status = xhr.status;
      reject(error);
    });

    xhr.addEventListener("error", () => reject(new Error("The upload failed.")));
    xhr.addEventListener("abort", () => reject(new Error("The upload was cancelled.")));
    xhr.send(form);
  });
}

export function uploadYouTube({ url, label = "", language = "", max_duration = 180 } = {}) {
  return request("/api/upload/youtube", {
    method: "POST",
    body: JSON.stringify({ url, label, language, max_duration }),
  });
}

/** Polls until the ingest settles. Whisper on CPU takes seconds to minutes. */
export async function waitForUpload(jobId, { intervalMs = 1000, timeoutMs = 600000 } = {}) {
  const deadline = Date.now() + timeoutMs;
  let notFoundRetries = 0;
  let job = null;

  while (Date.now() <= deadline) {
    try {
      job = await uploadJob(jobId);
      notFoundRetries = 0;
    } catch (err) {
      if (err.status === 404 && notFoundRetries < 3) {
        notFoundRetries++;
        await new Promise((resolve) => setTimeout(resolve, 1500));
        continue;
      }
      throw err;
    }

    if (job.status !== "queued" && job.status !== "running") {
      return job;
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error(`The ingest timed out (${job?.status ?? "unknown"})`);
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
