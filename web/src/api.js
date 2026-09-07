/**
 * Sunucu çağrıları.
 *
 * Oturum çerezi HttpOnly, yani JS onu okumuyor — fetch otomatik gönderiyor.
 * `credentials: "same-origin"` bunu açıkça yazıyor: aynı origin'deyiz ve
 * ajanın devraldığı oturum bağlamı bu çerezle taşınıyor.
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
    // Gövdesiz hata cevapları olabilir
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

/** İş bitene kadar durumu yokluyor. Render CPU işi, süresi öngörülemez. */
export async function waitForRender(jobId, { intervalMs = 700, timeoutMs = 120000 } = {}) {
  const deadline = Date.now() + timeoutMs;
  let job = await renderStatus(jobId);
  while (job.status === "queued" || job.status === "running") {
    if (Date.now() > deadline) {
      throw new Error(`Render zaman aşımına uğradı (${job.status})`);
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
    job = await renderStatus(jobId);
  }
  return job;
}
