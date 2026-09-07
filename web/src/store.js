/**
 * Tek durum kaynağı.
 *
 * WebMCP araçları ve sayfa paneli AYNI store'u değiştiriyor. Bu tesadüf değil:
 * `get_timeline_state` aracının okuyacağı tek bir doğru yer olmak zorunda, yoksa
 * ajanın gördüğü timeline ile insanın gördüğü timeline ayrışır — ki bu ürünün
 * bütün iddiasını çürütür.
 *
 * React yok. Bu arayüzde React'in çözdüğü bir problem yok ve bir build adımı
 * eklemek Cloud Run imajına node aşaması getiriyor. İhtiyaç duyulan tek şey
 * abone olunabilen bir durum, o da aşağıdaki 40 satır.
 */

const state = {
  session: null,          // {role, credits, costs}
  library: null,          // ClickHouse istatistikleri
  query: { phrase: "", tone: "" },
  candidates: [],         // find_line sonucu
  timeline: [],           // önerilen kesim: [{id, ...aday}]
  playback: { playing: false, index: -1, offsetMs: 0 },
  status: { kind: "idle", message: "" },
  webmcp: { available: false, registered: 0 },
  // Sayfa içi sohbet. Harici ajanın yerine geçmiyor, ajanı OLMAYAN kullanıcı için.
  chat: { available: false, reason: "", messagesLeft: 0, busy: false, messages: [] },
  // Render işi. status: idle | queued | running | done | failed
  render: { status: "idle", jobId: null, downloadUrl: null, mode: null },
};

const listeners = new Set();

export function getState() {
  // Kopya döndürüyoruz: dışarıdan doğrudan mutasyon store'u sessizce bozar.
  return structuredClone(state);
}

export function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function notify() {
  for (const listener of listeners) {
    try {
      listener(state);
    } catch (error) {
      // Bir abonenin hatası diğerlerini düşürmesin
      console.error("store abonesi hata verdi", error);
    }
  }
}

export function patch(changes) {
  Object.assign(state, changes);
  notify();
}

export function setStatus(kind, message) {
  state.status = { kind, message };
  notify();
}

// --- Timeline işlemleri ---
// propose_cut, swap ve remove hepsi buradan geçiyor ki ajan ve insan aynı
// veriyi değiştirsin.

export function setTimeline(segments) {
  state.timeline = segments.map(normalizeSegment);
  notify();
  return state.timeline;
}

export function appendToTimeline(candidate) {
  state.timeline = [...state.timeline, normalizeSegment(candidate)];
  notify();
  return state.timeline;
}

export function removeFromTimeline(index) {
  state.timeline = state.timeline.filter((_, position) => position !== index);
  notify();
  return state.timeline;
}

export function clearTimeline() {
  state.timeline = [];
  notify();
}

export function setPlayback(playback) {
  state.playback = { ...state.playback, ...playback };
  notify();
}

export function setChat(chat) {
  state.chat = { ...state.chat, ...chat };
  notify();
}

/**
 * Sayfa içi asistanın sonucunu duruma uygular.
 *
 * Kural burada duruyor, sohbet çağrısının içinde değil: ajanın bulduğu adaylar ve
 * koyduğu öneri, WebMCP araçlarının değiştirdiği AYNI timeline'a gidiyor. İki giriş
 * kapısı için iki ayrı uygulama kuralı olsa store ayrışırdı.
 */
export function applyAgentResult({ candidates = [], proposal = [] } = {}) {
  if (candidates.length) {
    state.candidates = candidates;
  }

  // Öneri kimlik listesi; segmentleri bu turdan gelen adaylardan çözüyoruz.
  const known = new Map(
    (candidates.length ? candidates : state.candidates).map((item) => [item.id, item])
  );
  const resolved = proposal.map((id) => known.get(id)).filter(Boolean);
  const dropped = proposal.filter((id) => !known.has(id));

  if (resolved.length) {
    state.timeline = resolved.map(normalizeSegment);
  }

  notify();
  return {
    candidates: state.candidates.length,
    segments: state.timeline.length,
    // Sessizce yutmuyoruz: çözülemeyen kimlik varsa çağıran bilsin
    dropped,
  };
}

export function appendChatMessage(message) {
  state.chat = { ...state.chat, messages: [...state.chat.messages, message] };
  notify();
}

function normalizeSegment(candidate) {
  return {
    id: candidate.id,
    take_id: candidate.take_id,
    line_id: candidate.line_id,
    scene: candidate.scene ?? "",
    camera: candidate.camera ?? "",
    speaker: candidate.speaker ?? "",
    tone: candidate.tone ?? "neutral",
    tone_score: candidate.tone_score ?? 0,
    start_ms: candidate.start_ms,
    end_ms: candidate.end_ms,
    duration_ms: candidate.end_ms - candidate.start_ms,
    text: candidate.text ?? "",
    source_url: candidate.source_url ?? "",
    media_url: candidate.media_url ?? "",
  };
}

export function timelineDurationMs() {
  return state.timeline.reduce((total, segment) => total + segment.duration_ms, 0);
}
