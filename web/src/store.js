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
  // The words of each line a candidate sits in, keyed "take_id:line_id". Filled after a
  // search, and what makes the transcript clickable at word level.
  lines: {},
  // Which words the editor picked out. One selection at a time on purpose: two ranges on
  // screen with one "add" button would be ambiguous.
  selection: null,        // {key, from, to} — inclusive word indices
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

/**
 * Moves a segment to another position.
 *
 * Order is the edit. Reordering is the cheapest way to change how a cut reads, and it
 * costs nothing because nothing has been rendered — so it belongs next to append and
 * remove rather than behind the render step.
 *
 * Out-of-range indices are clamped rather than throwing: this is driven by a pointer
 * drag and by arrow keys, and both can overshoot the ends by one.
 */
export function reorderTimeline(from, to) {
  const last = state.timeline.length - 1;
  if (last < 0) return state.timeline;

  const source = Math.min(Math.max(from, 0), last);
  const target = Math.min(Math.max(to, 0), last);
  if (source === target) return state.timeline;

  const next = [...state.timeline];
  const [moved] = next.splice(source, 1);
  next.splice(target, 0, moved);
  state.timeline = next;
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

// --- Word-level selection ---
// The product's namesake. A phrase match gives you the matched range; picking words gives
// you any range inside the line, which is the difference between "this line" and "these
// three words of this line".

export function setLines(lines) {
  state.lines = { ...state.lines, ...lines };
  notify();
  return state.lines;
}

export function lineKey(candidate) {
  return `${candidate.take_id}:${candidate.line_id}`;
}

/**
 * Picks a word, or extends the pick to a range.
 *
 * `extend` comes from a shift-click or a shift-arrow. Extending across lines is not
 * allowed, because a cut cannot span two recordings at once: if the key differs the pick
 * simply moves.
 *
 * Clicking the single already-picked word clears it, so there is always a way out without
 * hunting for a deselect button.
 */
export function selectWord(key, index, { extend = false } = {}) {
  const words = state.lines[key];
  if (!words || index < 0 || index >= words.length) return state.selection;

  const current = state.selection;
  if (extend && current && current.key === key) {
    state.selection = {
      key,
      from: Math.min(current.from, index),
      to: Math.max(current.to, index),
    };
  } else if (current && current.key === key && current.from === index && current.to === index) {
    state.selection = null;
  } else {
    state.selection = { key, from: index, to: index };
  }

  notify();
  return state.selection;
}

export function clearSelection() {
  if (!state.selection) return null;
  state.selection = null;
  notify();
  return null;
}

/**
 * Turns the current pick into something that can be played, added or rendered.
 *
 * The result is shaped exactly like a candidate, so every path downstream — the player,
 * the timeline, the render request — treats a hand-picked range and a phrase match
 * identically. The id encodes the real start, which keeps it stable and lets the server
 * resolve it the same way.
 */
export function selectionSegment() {
  const selection = state.selection;
  if (!selection) return null;

  const words = state.lines[selection.key];
  if (!words) return null;

  const picked = words.slice(selection.from, selection.to + 1);
  if (!picked.length) return null;

  const [take_id, line_id] = selection.key.split(":");
  // Any candidate from this line carries the provenance the words themselves do not.
  const source = state.candidates.find(
    (candidate) => lineKey(candidate) === selection.key
  );
  if (!source) return null;

  const start_ms = picked[0].start_ms;
  const end_ms = picked.at(-1).end_ms;

  return {
    ...source,
    id: `${take_id}:${line_id}:${start_ms}`,
    line_id: Number(line_id),
    start_ms,
    end_ms,
    duration_ms: end_ms - start_ms,
    text: picked.map((word) => word.word).join(" "),
    words: picked.length,
  };
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
