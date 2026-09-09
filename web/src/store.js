/**
 * The single source of truth.
 *
 * The WebMCP tools and the on-page panel change the SAME store. That is not incidental:
 * get_timeline_state needs exactly one correct place to read from, or the timeline the
 * agent sees drifts from the timeline the human sees, which would undo the product's
 * entire claim.
 *
 * No React. Nothing in this interface is a problem React solves, and adding a build step
 * would put a node stage in the Cloud Run image. The only requirement is state you can
 * subscribe to, and that is the forty lines below.
 */

const state = {
  session: null,          // {role, credits, costs}
  library: null,          // ClickHouse statistics
  query: { phrase: "", tone: "" },
  candidates: [],         // the find_line result
  timeline: [],           // the proposed cut: [{id, ...candidate}]
  playback: { playing: false, index: -1, offsetMs: 0 },
  status: { kind: "idle", message: "" },
  webmcp: { available: false, registered: 0 },
  // The in-page assistant. Not a replacement for an external agent; for the visitor with NONE.
  chat: { available: false, reason: "", messagesLeft: 0, busy: false, messages: [] },
  // The render job. status: idle | queued | running | done | failed
  render: { status: "idle", jobId: null, downloadUrl: null, mode: null },
};

const listeners = new Set();

export function getState() {
  // A copy, because mutating this from outside would corrupt the store silently.
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
      // One subscriber's failure must not take down the others
      console.error("a store subscriber failed", error);
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

// --- Timeline operations ---
// propose_cut, swap and remove all go through here, so the agent and the human are
// changing the same data.

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
 * Applies the in-page assistant's result to the state.
 *
 * The rule lives here rather than inside the chat call: the candidates the agent found
 * and the proposal it placed go to the SAME timeline the WebMCP tools change. Two entry
 * points with two separate application rules would let the store drift.
 */
export function applyAgentResult({ candidates = [], proposal = [] } = {}) {
  if (candidates.length) {
    state.candidates = candidates;
  }

  // The proposal is a list of ids; segments are resolved from this turn's candidates.
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
    // Not swallowed silently: if an id could not be resolved, the caller should know
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
